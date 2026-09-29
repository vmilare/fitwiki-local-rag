"""Saves, for each ask-mode test question: retrieved passages + source paths, the actual answer, citations, timings.
usage: .venv/bin/python tests/capture_evidence.py <label>"""
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fitwiki.harness import Harness
label = sys.argv[1]
T = json.loads((ROOT / "tests" / "questions.json").read_text())
qs = T["ask_answerable"] + T["ask_unsupported"] + ([T["ask_new_source"]] if (ROOT / "vault/Interview Basics/Coffee Chat Etiquette.md").exists() else [])
h, out = Harness(), [f"# Ask-mode evidence ({label}, {time.strftime('%Y-%m-%d %H:%M:%S')})\n"]
for t in qs:
    r = h.ask(t["q"])
    out.append(f"## {t['id']}: {t['q']}\n\n**Retrieved passages (top {len(r.hits)}):**\n")
    for x in r.hits:
        out.append(f"{x.rank}. `{x.path}` — {x.heading} (cosine {x.vec_score:.2f}, keyword {x.kw_score:.1f})\n   > " + x.text.replace("\n", "\n   > ")[:600])
    out.append(f"\n**Status:** {r.status} ({r.note or 'ok'})\n\n**Answer:**\n\n> {r.answer}\n")
    out.append("**Citations:** " + ("; ".join(f"[{c['n']}] `{c['path']}` — {c['heading']} ({c['source_ref']})" for c in r.citations) or "none"))
    out.append(f"\n**Timings:** {r.stats}\n")
(ROOT / "tests" / "results" / f"evidence-{label}.md").write_text("\n".join(out))
print("\n".join(out))
