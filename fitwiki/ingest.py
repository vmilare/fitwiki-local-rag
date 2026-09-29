"""Ingest = read original (never modified) -> drafts -> records -> vault -> retrieval index."""
import hashlib
import shutil
from dataclasses import asdict
from pathlib import Path

from . import store, wiki
from .config import SOURCES
from .sources import read_source


def ingest(path_str: str) -> dict:
    path = Path(path_str).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No such file: {path}")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    SOURCES.mkdir(exist_ok=True)
    copy = SOURCES / path.name
    if not copy.exists() or hashlib.sha256(copy.read_bytes()).hexdigest() != sha:
        shutil.copy2(path, copy)                      # read-only working copy; original untouched
    src, docs = read_source(path)
    rec = wiki.load_records()
    for old, meta in list(rec["sources"].items()):     # same original file re-ingested (even if its title changed): replace, never duplicate
        if meta["filename"] == src.filename:
            rec["docs"] = [d for d in rec["docs"] if d["source_title"] != old]
            del rec["sources"][old]
    rec["docs"] = [d for d in rec["docs"] if d["source_title"] != src.title] + [asdict(d) for d in docs]
    rec["sources"][src.title] = {"filename": src.filename, "kind": src.kind, "description": src.description, "sha256": sha}
    wiki.save_records(rec)                            # only after readers succeeded
    try:
        report = wiki.build_vault()
    except ValueError:
        raise
    report["index"] = store.sync()
    report["source"] = src.title
    report["notes_from_source"] = len(docs)
    return report
