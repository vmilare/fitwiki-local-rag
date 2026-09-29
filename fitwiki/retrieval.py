"""THE RETRIEVAL TOOL: query -> ranked original passages. No generation happens here.
Hybrid ranking = embedding cosine + BM25 keyword score, fused with reciprocal rank fusion."""
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

import numpy as np

from . import llm, store

STOP = set("a an and are as at be by do does for from how i in is it of on or the to was what when which who why with you your me my "
           "o os as um uma de do da dos das em no na que para com por foi se ao e".split())


def _tok(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return [w for w in re.findall(r"[a-z0-9]+", t) if w not in STOP and len(w) > 1]


@dataclass
class Hit:
    rank: int
    path: str
    title: str
    heading: str
    text: str
    source: str
    source_ref: str
    vec_score: float      # cosine similarity of query vs passage (0..1); used as the relevance signal
    kw_score: float       # BM25 keyword score
    fused: float


def search(query: str, k: int = 5) -> list[Hit]:
    chunks, vecs = store.load()
    if not chunks:
        return []
    q = llm.embed(["search_query: " + query])[0]
    cos = vecs @ q
    # BM25 over the same text the embedder saw
    docs = [_tok(c["embed_text"]) for c in chunks]
    df = Counter(w for d in docs for w in set(d))
    avg = sum(len(d) for d in docs) / len(docs)
    qt = _tok(query)
    bm = np.zeros(len(chunks))
    for i, d in enumerate(docs):
        tf = Counter(d)
        for w in qt:
            if w in tf:
                idf = math.log(1 + (len(docs) - df[w] + .5) / (df[w] + .5))
                bm[i] += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(d) / avg))
    rank_v = {i: r for r, i in enumerate(np.argsort(-cos))}
    rank_k = {i: r for r, i in enumerate(np.argsort(-bm))}
    fused = {i: 1 / (60 + rank_v[i]) + (1 / (60 + rank_k[i]) if bm[i] > 0 else 0) for i in range(len(chunks))}
    top = sorted(fused, key=fused.get, reverse=True)[:k]
    return [Hit(r + 1, chunks[i]["path"], chunks[i]["title"], chunks[i]["heading"], chunks[i]["text"], chunks[i]["source"],
                chunks[i]["source_ref"], float(cos[i]), float(bm[i]), fused[i]) for r, i in enumerate(top)]
