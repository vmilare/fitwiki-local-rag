"""THE HARNESS: everything around the model. It owns the modes, the context window, prompt files, tool calls,
model calls, citation checks, error handling, timing/memory metrics and saved outputs."""
import json
import re
import subprocess
import time
from pathlib import Path

from . import llm, rag, retrieval, store
from . import ingest as ingest_mod
from .config import CHAT_HISTORY_CHARS, OUTPUTS, PROMPTS, TOP_K
from .llm import LLMError

HELP = """fitwiki: local personal wiki (Gemma via Ollama + RAG). Everything runs on this machine.

  chat              Interactive assistant with personality and conversation memory. Uses your wiki automatically only when a
                    message needs it (a routing step decides); casual chat and drafting stay note-free.
                    Inside chat: /ask <q>  /search <q>  /notes on|off  /reset  /help  /quit
  ask "question"    Standalone, neutral answer built only from retrieved wiki passages, with [n] citations,
                    or INSUFFICIENT_EVIDENCE. Never sees chat history.
  search "query"    Raw retrieval: original passages + file paths. No model-generated answer.
  ingest <file>     Add a source (.pptx .xlsx .md .txt): originals stay untouched; rebuilds notes, index.md and the search index.
  status            Vault/index size, model, and memory used by Ollama.
  help              This text.

Prompts live in prompts/assistant.md (chat persona) and prompts/research_rules.md (ask rules), kept separate.
Local only: no cloud calls, no fallback. Outputs are saved under outputs/.
"""


def ollama_rss_mb() -> float:
    """Resident memory of all ollama processes (server + model runner), in MB."""
    out = subprocess.run(["ps", "-axo", "rss=,comm="], capture_output=True, text=True).stdout
    return round(sum(int(l.split(None, 1)[0]) for l in out.splitlines() if "ollama" in l.lower()) / 1024, 1)


def memory_snapshot() -> dict:
    models = llm.loaded_models()
    return {"ollama_rss_mb": ollama_rss_mb(),
            "loaded": [{"name": m["name"], "size_mb": round(m.get("size", 0) / 1e6), "vram_mb": round(m.get("size_vram", 0) / 1e6)} for m in models]}


class Harness:
    def __init__(self):
        self.history: list[dict] = []
        self.use_notes = True                 # /notes off disables wiki lookup in chat
        self.carry: list = []                 # passages used on the previous turn, kept for short follow-ups
        self.session = time.strftime("%Y%m%d-%H%M%S")

    # ---- chat: persona + rolling context, no retrieval
    def _trim(self):
        while len(self.history) > 2 and sum(len(m["content"]) for m in self.history) > CHAT_HISTORY_CHARS:
            self.history = self.history[2:]            # drop oldest user/assistant pair

    def _route(self, text: str) -> str:
        """Routing step: a short model call decides whether this message needs the user's wiki (the retrieval tool)."""
        chunks, _ = store.load()
        if not chunks:
            return "NO"
        titles = "\n".join("- " + t for t in sorted({c["title"] for c in chunks}))
        sysm = (PROMPTS / "router.md").read_text().replace("{titles}", titles)
        prev = ""
        if self.carry and self.history:
            prev = f"PREVIOUS_TURN (used wiki notes):\nuser: {self.history[-2]['content'][:200]}\nassistant: {self.history[-1]['content'][:300]}\n\n"
        r = llm.chat([{"role": "system", "content": sysm}, {"role": "user", "content": prev + "LATEST MESSAGE: " + text}],
                     temperature=0.0, num_predict=3)
        return r.text.strip().upper()

    def chat(self, text: str, on_token=None):
        persona = (PROMPTS / "assistant.md").read_text()
        used, route_s = [], 0.0
        if self.use_notes:
            t0 = time.perf_counter()
            route = self._route(text)
            if route.startswith("YES"):
                used = [x for x in retrieval.search(text, 4) if x.vec_score >= rag.MIN_TOP_COSINE - 0.05]
            elif route.startswith("FOLLOW") and self.carry:      # continues the last reply: keep its notes
                used = self.carry
            route_s = round(time.perf_counter() - t0, 2)
        self.carry = used
        system = persona
        if used:
            system += "\n\n# NOTES (passages from the user's own wiki for this turn)\n\n" + rag._evidence(used)
        self.history.append({"role": "user", "content": text})
        self._trim()
        try:
            res = llm.chat([{"role": "system", "content": system}] + self.history, temperature=0.8, on_token=on_token)
        except LLMError:
            self.history.pop()
            raise
        self.history.append({"role": "assistant", "content": res.text})   # retrieved passages are NOT stored in history
        res.stats["route_s"] = route_s
        res.stats["notes_used"] = sorted({x.path for x in used})
        d = OUTPUTS / "chat"; d.mkdir(parents=True, exist_ok=True)
        with open(d / f"{self.session}.jsonl", "a") as f:
            f.write(json.dumps({"user": text, "assistant": res.text, "stats": res.stats}, ensure_ascii=False) + "\n")
        return res

    def reset_chat(self):
        self.history = []
        self.session = time.strftime("%Y%m%d-%H%M%S")

    # ---- ask: stateless, evidence only
    def ask(self, question: str) -> rag.AskResult:
        t0 = time.perf_counter()
        r = rag.ask(question)                            # note: self.history is never passed in
        r.stats["pipeline_s"] = round(time.perf_counter() - t0, 2)
        self._save_ask(r)
        return r

    def _save_ask(self, r: rag.AskResult):
        d = OUTPUTS / "ask"; d.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", r.question.lower())[:40].strip("-")
        cites = "\n".join(f"- [{c['n']}] `{c['path']}` — {c['heading']} ({c['source_ref']})" for c in r.citations) or "- none"
        (d / f"{time.strftime('%Y%m%d-%H%M%S')} {slug}.md").write_text(
            f"# {r.question}\n\n**Status:** {r.status} ({r.note or 'ok'})\n\n{r.answer}\n\n## Citations\n\n{cites}\n\n"
            f"## Stats\n\n```json\n{json.dumps(r.stats)}\n```\n")

    # ---- search: raw passages, no generation
    def search(self, query: str, k: int = TOP_K):
        return retrieval.search(query, k)

    def ingest(self, path: str) -> dict:
        return ingest_mod.ingest(path)

    def status(self) -> dict:
        from . import store
        chunks, _ = store.load()
        from .config import VAULT
        return {"notes": len(list(VAULT.rglob("*.md"))), "chunks": len(chunks), "memory": memory_snapshot()}
