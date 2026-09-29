"""Runs every required test through the real harness and records what actually happened.
usage: .venv/bin/python tests/run_tests.py <label>     (label e.g. online / offline)"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fitwiki import llm, retrieval
from fitwiki.harness import Harness, memory_snapshot, ollama_rss_mb

label = sys.argv[1] if len(sys.argv) > 1 else "run"
T = json.loads((ROOT / "tests" / "questions.json").read_text())
calls = {"chat": 0, "search": 0}
_c, _s = llm.chat, retrieval.search
llm.chat = lambda *a, **k: (calls.__setitem__("chat", calls["chat"] + 1), _c(*a, **k))[1]
retrieval.search = lambda *a, **k: (calls.__setitem__("search", calls["search"] + 1), _s(*a, **k))[1]


class Peak:
    """Samples Ollama's resident memory every 0.25 s while a test runs."""
    def __enter__(self):
        self.peak, self.run = ollama_rss_mb(), True
        def loop():
            while self.run:
                self.peak = max(self.peak, ollama_rss_mb()); time.sleep(0.25)
        self.t = threading.Thread(target=loop, daemon=True); self.t.start(); self.t0 = time.perf_counter(); return self
    def __exit__(self, *a):
        self.run = False; self.t.join(); self.secs = round(time.perf_counter() - self.t0, 2)


def sysmem():
    swap = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout.strip()
    mp = subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout
    free = [l for l in mp.splitlines() if "free percentage" in l]
    return {"swap": swap, "system_free_pct": free[0].split(":")[1].strip() if free else None}


results, h = [], Harness()
print(f"== {label} | memory before:", memory_snapshot(), sysmem())

def rec(name, **kw):
    results.append({"test": name, **kw}); ok = kw.get("pass")
    print(f"[{'PASS' if ok else 'FAIL' if ok is False else 'INFO'}] {name}: {kw.get('seconds')}s  peak_ollama_rss={kw.get('peak_ollama_rss_mb')}MB")

extra = [T["ask_new_source"]] if (ROOT / "vault" / "Interview Basics" / "Coffee Chat Etiquette.md").exists() else []
for t in T["ask_answerable"] + T["ask_unsupported"] + extra:
    with Peak() as p:
        r = h.ask(t["q"])
    if "expect_all" in t:
        txt = r.answer.lower()
        miss = [w for w in t["expect_all"] if w.lower() not in txt]
        paths_ok = any(t["expect_path"] in c["path"] for c in r.citations)
        ok = r.status == "answered" and not miss and paths_ok
        detail = {"missing_terms": miss, "cited_expected_path": paths_ok}
    else:
        ok = r.status == "insufficient"; detail = {}
    rec(f"ask {t['id']}", question=t["q"], status=r.status, answer=r.answer, citations=r.citations, note=r.note,
        model_stats=r.stats, seconds=p.secs, peak_ollama_rss_mb=p.peak, **{"pass": ok}, **detail)

# raw search: must not call the model
before = calls["chat"]
with Peak() as p:
    hits = h.search(T["search"]["q"])
ok = calls["chat"] == before and any(T["search"]["expect_path"] in x.path for x in hits[:3])
rec("search raw", query=T["search"]["q"], results=[{"path": x.path, "heading": x.heading, "cosine": round(x.vec_score, 3)} for x in hits],
    model_called=calls["chat"] != before, seconds=p.secs, peak_ollama_rss_mb=p.peak, **{"pass": ok})

# casual chat + drafting: must not trigger retrieval
h2, before = Harness(), calls["search"]
for name, prompt in (("chat casual", T["chat_casual"]), ("chat drafting", T["chat_draft"])):
    with Peak() as p:
        r = h2.chat(prompt)
    rec(name, prompt=prompt, response=r.text, model_stats=r.stats, retrieval_calls=calls["search"] - before, seconds=p.secs,
        peak_ollama_rss_mb=p.peak, notes_used=r.stats.get("notes_used"), **{"pass": bool(r.text) and calls["search"] == before and not r.stats.get("notes_used")})

# notes-aware chat: a message about the user's own story must pull the right note into context
hn = Harness()
with Peak() as p:
    r = hn.chat(T["chat_with_notes"]["prompt"])
used = r.stats.get("notes_used", [])
rec("chat uses wiki notes", prompt=T["chat_with_notes"]["prompt"], response=r.text, notes_used=used, model_stats=r.stats,
    seconds=p.secs, peak_ollama_rss_mb=p.peak,
    **{"pass": any(T["chat_with_notes"]["expect_path"] in u for u in used) and T["chat_with_notes"]["expect_word"] in r.text})

# follow-up: second turn must refer to the first turn's content
h3 = Harness()
r1 = h3.chat(T["chat_followup"][0])
with Peak() as p:
    r2 = h3.chat(T["chat_followup"][1])
words = {w.strip(".,!*'\"").lower()[:5] for w in r1.text.replace("\u2581", " ").split() if len(w) > 4}   # stems: Bubbles ~ Bubble
overlap = sorted(w for w in words if w in r2.text.lower())
rec("chat follow-up", turn1=r1.text, turn2=r2.text, shared_words=overlap[:10], seconds=p.secs, peak_ollama_rss_mb=p.peak,
    model_stats=r2.stats, **{"pass": bool(overlap)}, note="heuristic overlap check; read the two turns to judge")

# separation: a fact given in chat must be invisible to ask, and ask must not enter chat history
h4 = Harness()
h4.chat(T["separation"]["fact"])
n_before = len(h4.history)
with Peak() as p:
    r = h4.ask(T["separation"]["ask"])
rec("ask/chat separation", answer=r.answer, status=r.status, history_len_before=n_before, history_len_after=len(h4.history),
    seconds=p.secs, peak_ollama_rss_mb=p.peak, **{"pass": r.status == "insufficient" and len(h4.history) == n_before})

after = {"memory_after": memory_snapshot(), "system": sysmem()}
print("== memory after:", after)
out = ROOT / "tests" / "results" / f"{label}.json"
out.write_text(json.dumps({"label": label, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results, **after}, indent=1, ensure_ascii=False))
print(f"\n{sum(1 for r in results if r.get('pass'))}/{sum(1 for r in results if 'pass' in r)} passed -> {out}")
