"""THE RAG WORKFLOW for ask mode: retrieve -> gate -> ground the prompt in evidence -> generate -> verify citations.
It calls the retrieval tool and the model; it is one fixed pipeline, not a conversation."""
import re
import time
from dataclasses import dataclass, field

from . import llm, retrieval
from .config import PROMPTS, TOP_K

MIN_TOP_COSINE = 0.50     # below this, nothing in the wiki is even topically close: skip the model (calibrated in tests)
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


@dataclass
class AskResult:
    question: str
    status: str                    # answered | insufficient | uncited | error
    answer: str
    citations: list = field(default_factory=list)   # [{n, path, heading, source_ref}]
    hits: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    note: str = ""


def _evidence(hits) -> str:
    return "\n\n".join(f"[{h.rank}] {h.path} — {h.heading}\n{h.text}" for h in hits)


def _cited(text: str) -> list[int]:
    nums = []
    for grp in re.findall(r"\[([\d,\s]+)\]", text):
        nums += [int(x) for x in re.findall(r"\d+", grp)]
    return nums


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.lower())


def _bad_terms(answer: str, evidence: str, question: str) -> list[str]:
    """Capitalised words in the answer that appear nowhere in the evidence or question: catches garbled names
    (e.g. 'Performa') and invented proper nouns. Sentence-initial words and the [n] markers are ignored."""
    known = set(re.findall(r"[\w']+", _norm(evidence + " " + question)))
    body = re.sub(r"\[[\d,\s]+\]", " ", answer)
    words = []
    for sent in re.split(r"(?<=[.!?:])\s+|\n", body):
        toks = re.findall(r"[\w'&-]+", sent)
        words += toks[1:]
    return sorted({w for w in words if w[:1].isupper() and len(w) >= 4 and w.lower() not in known})


def _add(a: dict, b: dict) -> dict:
    return {k: (round(a.get(k, 0) + b.get(k, 0), 2) if isinstance(b.get(k), (int, float)) else b.get(k)) for k in b}


def ask(question: str, k: int = TOP_K) -> AskResult:
    t0 = time.perf_counter()
    hits = retrieval.search(question, k)
    retrieval_s = round(time.perf_counter() - t0, 2)
    if not hits or max(h.vec_score for h in hits) < MIN_TOP_COSINE:
        return AskResult(question, "insufficient", f"{INSUFFICIENT}: nothing in the wiki is close enough to this question.",
                         hits=hits, note="retrieval gate (model not called)")
    rules = (PROMPTS / "research_rules.md").read_text()
    user = f"EVIDENCE\n\n{_evidence(hits)}\n\nQUESTION: {question}"
    messages = [{"role": "system", "content": rules}, {"role": "user", "content": user}]   # no chat history, by design
    res = llm.chat(messages, temperature=0.0, num_predict=400)
    res.stats["retrieval_s"] = retrieval_s
    text, note = res.text, ""
    bad = _bad_terms(text, _evidence(hits), question)
    if bad and not text.startswith(INSUFFICIENT):     # grounding check; one corrective retry
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "These words are not in the EVIDENCE: " + ", ".join(bad) +
                      ". Rewrite the answer copying names and titles exactly as written in the EVIDENCE, keeping [n] citations."}]
        res2 = llm.chat(messages, temperature=0.0, num_predict=400)
        res.stats, text = _add(res.stats, res2.stats), res2.text
        messages = messages[:-2]
        if _bad_terms(text, _evidence(hits), question):
            note = "some capitalised terms could not be matched to the evidence; verify against the cited passage"
    if text.startswith(INSUFFICIENT) or INSUFFICIENT in text[:40]:
        return AskResult(question, "insufficient", text, hits=hits, stats=res.stats, note="model judged evidence insufficient")
    valid = sorted({n for n in _cited(text) if 1 <= n <= len(hits)})
    if not valid:      # one retry with a stricter reminder
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": "Rewrite your answer so every statement ends with a [n] citation from the EVIDENCE, "
                                                      f"or reply {INSUFFICIENT}: <reason>."})
        res2 = llm.chat(messages, temperature=0.0, num_predict=400)
        res.stats = _add(res.stats, res2.stats)
        text = res2.text
        if text.startswith(INSUFFICIENT):
            return AskResult(question, "insufficient", text, hits=hits, stats=res.stats, note="model judged evidence insufficient (after retry)")
        valid = sorted({n for n in _cited(text) if 1 <= n <= len(hits)})
    cites = [{"n": n, "path": hits[n - 1].path, "heading": hits[n - 1].heading, "source_ref": hits[n - 1].source_ref} for n in valid]
    if not cites:
        return AskResult(question, "uncited", text, hits=hits, stats=res.stats, note="model gave no valid citation; treat as unverified")
    return AskResult(question, "answered", text, cites, hits, res.stats, note)
