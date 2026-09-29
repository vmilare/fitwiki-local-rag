"""Retrieval chunks + vectors. Stored in .index/, never inside the vault."""
import hashlib
import json
import re

import numpy as np

from .config import EXCLUDED_FROM_RETRIEVAL, INDEX, VAULT
from . import llm

CHUNKS = INDEX / "chunks.json"
VECTORS = INDEX / "vectors.npy"
MAX_CHARS = 1400


def _parse_note(text: str):
    meta = {}
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                try: meta[k.strip()] = json.loads(v.strip())
                except Exception: meta[k.strip()] = v.strip()
        text = text[m.end():]
    return meta, text


def _plain(t: str) -> str:
    return re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", re.sub(r"\[\[([^\]|]+)\]\]", r"\1", t))


def _split_section(heading: str, body: str) -> list[tuple[str, str]]:
    """Split one H2 section into <= MAX_CHARS pieces, at H3 then paragraph boundaries."""
    body = body.strip()
    if len(body) <= MAX_CHARS:
        return [(heading, body)]
    lead = body.split("\n\n")[0][:220]
    pieces, cur, cur_head = [], "", heading
    for block in re.split(r"\n\n(?=### )", body):
        sub = re.match(r"### (.+)", block)
        for para in block.split("\n\n"):
            if len(cur) + len(para) > MAX_CHARS and cur:
                pieces.append((cur_head, cur.strip())); cur = ""
                cur = (lead + "\n\n") if pieces and not para.startswith(lead[:40]) else ""
            cur += para + "\n\n"
        if sub:
            cur_head = f"{heading} > {sub.group(1)}"
    if cur.strip():
        pieces.append((cur_head, cur.strip()))
    return pieces


def build_chunks() -> list[dict]:
    chunks = []
    for path in sorted(VAULT.rglob("*.md")):
        rel = path.relative_to(VAULT)
        if rel.name in EXCLUDED_FROM_RETRIEVAL or rel.parts[0] in EXCLUDED_FROM_RETRIEVAL:
            continue
        meta, text = _parse_note(path.read_text())
        title = meta.get("title", path.stem)
        text = re.sub(r"^# .+\n", "", text.strip(), count=1)
        text = re.split(r"\n## (?:Sources|Related)\n", "\n" + text)[0]
        parts = re.split(r"\n(?=## )", "\n" + text)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            m = re.match(r"## (.+)\n?", part)
            heading, body = (m.group(1).strip(), part[m.end():]) if m else (title, part)
            for h, piece in _split_section(heading, body):
                piece = _plain(piece).strip()
                if len(piece) < 15:
                    continue
                cid = hashlib.sha1(f"{rel}|{h}|{piece}".encode()).hexdigest()[:12]
                chunks.append({"id": cid, "path": f"vault/{rel}", "title": title, "heading": h, "text": piece,
                               "source": _plain(str(meta.get("source", ""))), "source_ref": meta.get("source_ref", ""),
                               "embed_text": f"search_document: {title} — {h}\n{piece}"})
    return chunks


def sync() -> dict:
    """Bring the index in line with the vault; only new/changed chunks are embedded."""
    chunks = build_chunks()
    old = {}
    if CHUNKS.exists() and VECTORS.exists():
        oc, ov = json.loads(CHUNKS.read_text()), np.load(VECTORS)
        old = {c["id"]: ov[i] for i, c in enumerate(oc)}
    need = [c for c in chunks if c["id"] not in old]
    if need:
        fresh = llm.embed([c["embed_text"] for c in need])
        old.update({c["id"]: fresh[i] for i, c in enumerate(need)})
    INDEX.mkdir(exist_ok=True)
    vecs = np.stack([old[c["id"]] for c in chunks]) if chunks else np.zeros((0, 1), np.float32)
    CHUNKS.write_text(json.dumps(chunks, ensure_ascii=False))
    np.save(VECTORS, vecs)
    return {"chunks": len(chunks), "embedded": len(need), "reused": len(chunks) - len(need)}


def load():
    if not (CHUNKS.exists() and VECTORS.exists()):
        return [], np.zeros((0, 1), np.float32)
    return json.loads(CHUNKS.read_text()), np.load(VECTORS)
