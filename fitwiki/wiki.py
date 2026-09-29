"""Vault writer. Notes are regenerated from records kept in .index/, so re-ingesting can never duplicate
notes or restore old names. Hand-edited generated notes are detected and left alone."""
import hashlib
import json
import re
import shutil
import time
from dataclasses import asdict

from . import naming
from .config import INDEX, VAULT
from .sources import Doc, curation

RECORDS = INDEX / "records.json"     # canonical drafts of every note + source metadata
MANIFEST = INDEX / "manifest.json"   # vault path -> hash of what we last wrote


def _load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def load_records() -> dict:
    return _load(RECORDS, {"docs": [], "sources": {}})


def save_records(rec: dict) -> None:
    INDEX.mkdir(exist_ok=True)
    RECORDS.write_text(json.dumps(rec, ensure_ascii=False, indent=1))


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _yaml(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def summary_of(d: Doc) -> str:
    if d.get("summary"):
        return d["summary"]
    body = d["body"]
    if d["kind"] == "project":
        n = len(re.findall(r"^## Episode", body, re.M))
        return f"{n} detailed STARL episode{'s' if n != 1 else ''} from this project (Portuguese)"
    if d["kind"] == "story":
        m = re.match(r"\*\*Competencies:\*\* (.*)", body)
        return "STARL story in English · " + (m.group(1) if m else "")
    if d["kind"] == "questions":
        n = len(re.findall(r"^- ", body, re.M))
        return f"{n} questions"
    if d["kind"] == "map":
        return "Which stories to use for each interview question"
    text = body.split("## Answer", 1)[-1] if "## Answer" in body else body
    text = re.sub(r"\*\*Interview question:\*\*.*\n", "", text)
    text = re.sub(r"[#*\-\[\]|]", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return (text[:110].rsplit(" ", 1)[0] + "…") if len(text) > 110 else text


def _link_pass(body: str, self_title: str, terms: dict[str, str]) -> str:
    """Wrap the first mention of each known term in [[Note Title|term]] (once per note)."""
    used, out = set(), []
    for line in body.split("\n"):
        if line.startswith("#"):
            out.append(line); continue
        segs = re.split(r"(\[\[.*?\]\])", line)
        for i, seg in enumerate(segs):
            if seg.startswith("[["):
                m = re.match(r"\[\[([^\]|]+)", seg)
                if m: used.add(m.group(1))
                continue
            for term, target in terms:
                if target == self_title or target in used:
                    continue
                m = re.search(r"(?<![\w\[])" + re.escape(term) + r"(?![\w\]])", seg, re.I)
                if m:
                    shown = m.group(0)
                    link = f"[[{target}]]" if shown == target else f"[[{target}|{shown}]]"
                    seg = seg[:m.start()] + link + seg[m.end():]
                    used.add(target)
            segs[i] = seg
        out.append("".join(segs))
    return "\n".join(out)


def _compose(d: dict, terms, titles: set, comp_note: str, cur: dict) -> str:
    fm = ["---", f"title: {_yaml(d['title'])}", f"type: {d['kind']}", f"topic: {_yaml(d['folder'])}",
          f"language: {d['language']}", f"tags: {_yaml(d['tags'])}",
          f"source: {_yaml('[[' + d['source_title'] + ']]')}", f"source_ref: {_yaml(d['source_ref'])}",
          f"note_id: {_yaml(d['note_id'])}", "generated: true", "---", ""]
    body = _link_pass(d["body"], d["title"], terms)
    rel = []
    for r in d.get("related", []):
        if r in titles and r != d["title"]:
            rel.append(f"- [[{r}]]")
        elif r in cur["competencies"].values() and comp_note in titles:
            rel.append(f"- [[{comp_note}|{r}]]")
    out = "\n".join(fm) + f"# {d['title']}\n\n{body.strip()}\n"
    if rel:
        out += "\n## Related\n\n" + "\n".join(dict.fromkeys(rel)) + "\n"
    out += f"\n## Sources\n\n- [[{d['source_title']}]] — {d['source_ref']}\n"
    return out


def build_vault() -> dict:
    cur, rec = curation(), load_records()
    docs = rec["docs"]
    # ---- validation: naming rules + global uniqueness (Obsidian resolves links by unique filename)
    seen, errors = {}, []
    for d in docs:
        for p in naming.problems(d["title"]):
            errors.append(f"{d['title']!r}: {p}")
        if d["title"].lower() in seen:
            errors.append(f"duplicate title {d['title']!r} ({d['note_id']} vs {seen[d['title'].lower()]})")
        seen[d["title"].lower()] = d["note_id"]
        if d["folder"] not in cur["folders"]:
            errors.append(f"{d['title']!r}: unknown folder {d['folder']!r}")
    for t in rec["sources"]:
        for p in naming.problems(t):
            errors.append(f"source {t!r}: {p}")
        if t.lower() in seen:      # a source note and a content note must never share a filename
            errors.append(f"source title {t!r} collides with note {seen[t.lower()]}")
        seen[t.lower()] = "source"
    if errors:
        raise ValueError("Naming/structure check failed:\n  " + "\n  ".join(errors))

    titles = {d["title"] for d in docs} | set(rec["sources"])
    terms = {t: n for t, n in cur["link_terms"].items() if n in titles}
    terms.update({d["title"]: d["title"] for d in docs if len(d["title"].split()) >= 2})
    terms = sorted(terms.items(), key=lambda kv: -len(kv[0]))
    comp_note = "Competency Areas"

    files: dict[str, str] = {}
    for d in docs:
        files[f"{d['folder']}/{d['title']}.md"] = _compose(d, terms, titles, comp_note, cur)

    for stitle, s in rec["sources"].items():
        mine = [d for d in docs if d["source_title"] == stitle]
        groups = "\n".join(f"### {f}\n\n" + "\n".join(f"- [[{d['title']}]] — {d['source_ref']}" for d in mine if d["folder"] == f) + "\n"
                           for f in cur["folders"] if any(d["folder"] == f for d in mine))
        files[f"Sources/{stitle}.md"] = (
            "---\n" f"title: {_yaml(stitle)}\ntype: source\ntopic: \"Sources\"\nsource_file: {_yaml(s['filename'])}\n"
            f"source_format: {s['kind']}\nsha256: {_yaml(s['sha256'][:16])}\nnote_id: {_yaml('source:' + s['sha256'][:8])}\ngenerated: true\n---\n"
            f"# {stitle}\n\n{s['description']}\n\n**Original file:** `{s['filename']}` (kept unchanged outside the vault, in `sources/`).\n\n"
            f"## Notes Derived From This Source\n\n{groups}")

    # ---- index.md grouped by topic
    sect = []
    for f in cur["folders"]:
        items = sorted((d for d in docs if d["folder"] == f), key=lambda d: d["title"])
        if f == "Sources":
            items = [{"title": t, "summary": s["description"]} for t, s in sorted(rec["sources"].items())]
            lines = [f"- [[{i['title']}]] — {i['summary']}" for i in items]
        else:
            lines = [f"- [[{d['title']}]] — {summary_of(d)}" for d in items]
        if lines:
            sect.append(f"## {f}\n\n" + "\n".join(lines))
    files["index.md"] = ("---\ntitle: \"Index\"\ntype: index\ngenerated: true\n---\n# Index\n\n"
                         "Personal wiki for consulting FIT interview preparation. Grouped by topic; every note links back to its source.\n\n"
                         + "\n\n".join(sect) + "\n")

    # ---- write (idempotent) with protection for hand-edited files
    man = _load(MANIFEST, {})
    report = {"written": [], "unchanged": 0, "archived": [], "skipped_edited": []}
    for rel, text in files.items():
        path = VAULT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            disk = path.read_text()
            if disk == text:
                report["unchanged"] += 1; man[rel] = _h(text); continue
            if man.get(rel) != _h(disk):       # differs from what we last wrote -> user edited it
                report["skipped_edited"].append(rel); continue
        path.write_text(text); man[rel] = _h(text); report["written"].append(rel)
    for rel in [r for r in man if r not in files]:   # no longer produced (renamed / removed upstream)
        path = VAULT / rel
        if path.exists() and man[rel] == _h(path.read_text()):
            dest = INDEX / "archive" / time.strftime("%Y%m%d-%H%M%S") / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), dest); report["archived"].append(rel)
        del man[rel]
    for d in sorted(VAULT.rglob("*"), reverse=True):   # drop empty folders
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    INDEX.mkdir(exist_ok=True)
    MANIFEST.write_text(json.dumps(man, indent=1, ensure_ascii=False))
    return report
