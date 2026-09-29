"""Source readers: turn an original file into Docs (draft notes). Originals are only ever read."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import CURATION
from .naming import clean_stem, heuristic_title, title_case

PT_WORDS = {"de", "que", "para", "com", "uma", "não", "em", "os", "as", "do", "da", "meu", "minha", "foi", "por"}


@dataclass
class Doc:
    note_id: str            # machine id, lives in frontmatter only
    title: str
    folder: str
    kind: str               # concept | answer | story | project | questions | map | source
    body: str               # markdown without H1 / Sources / Related (added by the wiki writer)
    source_title: str
    source_ref: str         # human-readable location, e.g. 'sheet "Historias", rows 3-6'
    language: str = "en"
    tags: list = field(default_factory=list)
    related: list = field(default_factory=list)
    summary: str = ""      # one-line description for index.md (curated when available)


@dataclass
class SourceInfo:
    title: str
    filename: str
    kind: str
    description: str


def curation() -> dict:
    return json.loads(CURATION.read_text())


def _lang(text: str) -> str:
    words = re.findall(r"[a-zà-ú]+", text.lower())
    return "pt" if words and sum(w in PT_WORDS for w in words) / len(words) > 0.08 else "en"


def _paras(text) -> str:
    """Cell text -> markdown paragraphs (source line breaks become blank lines)."""
    lines = [l.strip() for l in str(text).replace("\r", "").split("\n") if l.strip()]
    return "\n\n".join(lines)


def _match(rules: list[dict], text: str):
    t = text.lower()
    return next((r for r in rules if r["match"].lower() in t), None)


def project_title(cur: dict, story_label: str) -> str | None:
    """Map 'Project Name - Client: focus...' to its project note title (prefix before ':' only)."""
    prefix = story_label.split(":")[0]
    r = _match(cur["projects"], prefix)
    return r["title"] if r else None


def source_info(path: Path, cur: dict) -> SourceInfo:
    r = next((s for s in cur["sources"] if s["match"].lower() in path.name.lower()), None)
    title = r["title"] if r else clean_stem(path.stem) + " Source"   # never equal to a content note title (unique filenames)
    return SourceInfo(title, path.name, path.suffix.lower().lstrip("."), r["description"] if r else f"Ingested from {path.name}.")


# ---------------------------------------------------------------- PPTX
_NOISE = re.compile(r"^(\d/\d|[.…]+|\d+\.\s*NOT an exhaustive list)$", re.I)
_AGENDA = {"01", "02", "03", "04", "05"}


def read_pptx(path: Path, src: SourceInfo, cur: dict) -> list[Doc]:
    from pptx import Presentation
    prs = Presentation(str(path))
    slides = {i: s for i, s in enumerate(prs.slides, 1)}
    docs, seen_notes = [], set()      # the deck repeats identical speaker notes on several slides: keep each text once
    for rule in cur["deck_slides"]:
        drop = {d.lower() for d in rule.get("drop_lines", [])}
        parts, notes = [], []
        for n in rule["slides"]:
            s = slides[n]
            for sh in sorted(s.shapes, key=lambda x: (round((x.top or 0) / 300000), x.left or 0)):
                if sh.has_text_frame:
                    for p in sh.text_frame.paragraphs:
                        t = re.sub(r"(?<=[a-z])1$", "", re.sub(r"\s+", " ", "".join(r.text for r in p.runs)).strip())
                        if t and t.lower() not in drop and not _NOISE.match(t) and t not in _AGENDA:
                            parts.append(("  " * min(p.level, 3)) + "- " + t)
                elif getattr(sh, "has_table", False) and sh.has_table:
                    rows = [[c.text.strip() for c in r.cells] for r in sh.table.rows]
                    if all(not any(r[1:]) for r in rows[1:]):
                        parts.append("Frames of the question (columns are competency areas: " + ", ".join(h for h in rows[0][1:] if h) + "):")
                        parts += ["- " + r[0] for r in rows[1:] if r[0]]
                    else:
                        parts += ["| " + " | ".join(r) + " |" for r in rows]
            if not rule.get("no_notes") and s.has_notes_slide and s.notes_slide.notes_text_frame.text.strip():
                nt = _paras(s.notes_slide.notes_text_frame.text)
                if nt not in seen_notes:
                    seen_notes.add(nt); notes.append(nt)
        body = rule.get("body_override") or "\n".join(parts)
        if notes:
            body += "\n\n## Speaker Notes\n\n" + "\n\n".join(notes)
        refs = ", ".join(str(n) for n in rule["slides"])
        docs.append(Doc(f"pptx:{path.stem}:{rule['title']}".lower().replace(" ", "-"), rule["title"], rule["folder"], "concept",
                        body, src.title, f"slide{'s' if len(rule['slides']) > 1 else ''} {refs}", "en", ["fit", "interview-basics"], summary=rule.get("summary", "")))
    return docs


# ---------------------------------------------------------------- XLSX
def _headers(ws, row: int) -> dict:
    return {str(c.value).strip(): c.column for c in ws[row] if c.value not in (None, "")}


def _cell(ws, row, col):
    v = ws.cell(row=row, column=col).value
    return "" if v is None else str(v).strip()


def _find_row(ws, col_letter: str, text: str) -> int:
    for r in range(1, ws.max_row + 1):
        if str(ws[f"{col_letter}{r}"].value or "").strip().lower() == text.lower():
            return r
    raise ValueError(f"{text!r} not found in column {col_letter} of {ws.title!r}")


def read_xlsx(path: Path, src: SourceInfo, cur: dict) -> list[Doc]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    docs: list[Doc] = []
    s = src.title
    slug = lambda t: re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")

    # --- pitch answers (General Questions)
    if "General Questions" in wb.sheetnames:
        ws = wb["General Questions"]
        h = _find_row(ws, "C", "Stories")
        merged: dict[str, dict] = {}
        for r in range(h + 1, ws.max_row + 1):
            q, ans, kw = _cell(ws, r, 3), _cell(ws, r, 4), _cell(ws, r, 10)
            if not q or not ans:
                continue
            rule = _match(cur["general_questions"], q)
            title = rule["title"] if rule else heuristic_title(q)
            folder = rule["folder"] if rule else "Personal Pitch"
            m = merged.setdefault(title, {"summary": rule.get("summary", "") if rule else "", "folder": folder, "q": q, "ans": [], "kw": [], "rows": []})
            m["ans"].append(_paras(ans)); m["rows"].append(r)
            if kw: m["kw"].append(_paras(kw))
        for title, m in merged.items():
            body = f"**Interview question:** {m['q']}\n\n## Answer\n\n" + "\n\n".join(m["ans"])
            if m["kw"]:
                body += "\n\n## Key Words\n\n" + "\n\n".join(m["kw"])
            rows = m["rows"]
            docs.append(Doc(f"xlsx:general-questions:{slug(title)}", title, m["folder"], "answer", body, s,
                            f'sheet "General Questions", row{"s" if len(rows) > 1 else ""} {rows[0]}' + (f"-{rows[-1]}" if len(rows) > 1 else ""),
                            _lang(" ".join(m["ans"])), ["fit", "answer"], summary=m["summary"]))

    # --- English STARL drafts (FIT Prep Stories)
    if "FIT Prep Stories" in wb.sheetnames:
        ws = wb["FIT Prep Stories"]
        h = _find_row(ws, "C", "Stories")
        cols = {k.strip(): v for k, v in _headers(ws, h).items()}
        for r in range(h + 1, ws.max_row + 1):
            label = _cell(ws, r, cols["Stories"])
            num = _cell(ws, r, 2)
            if not label or not num.isdigit() or not _cell(ws, r, cols["Situation"]):
                continue
            rule = _match(cur["starl_stories"], label)
            if not rule:
                continue
            comps = [cur["competencies"][k] for k in cur["competencies"] if any(k.startswith(c) or c.startswith(k[:8]) for c in cols if c.strip() == k) and _cell(ws, r, cols[k]).lower() == "x"]
            parts = [f"**Competencies:** {', '.join(comps) or 'not tagged'}"]
            for sec in ("Situation", "Task", "Action", "Result", "Learnings"):
                v = _cell(ws, r, cols[sec])
                if v:
                    parts.append(f"## {sec}\n\n{_paras(v)}")
            kw = _cell(ws, r, cols.get("Key words to use", 0)) if "Key words to use" in cols else ""
            if kw:
                parts.append("## Key Words\n\n" + _paras(kw))
            d = Doc(f"xlsx:fit-prep-stories:{slug(rule['title'])}", rule["title"], "Stories", "story", "\n\n".join(parts), s,
                    f'sheet "FIT Prep Stories", row {r}', "en", ["fit", "starl"], related=[rule["project"]] + comps)
            docs.append(d)

    # --- Portuguese project stories (Historias): one note per project
    if "Historias" in wb.sheetnames:
        ws = wb["Historias"]
        h = _find_row(ws, "A", "PERGUNTAS QUE TEM RELAÇÃO")
        cols = _headers(ws, h)
        story_col = next(c for k, c in cols.items() if k.startswith("Lista de Hist"))
        projects: dict[str, list] = {}
        for r in range(h + 1, ws.max_row + 1):
            label = _cell(ws, r, story_col)
            if not label:
                continue
            title = project_title(cur, label) or heuristic_title(label.split(":")[0])
            projects.setdefault(title, []).append((r, label))
        for title, eps in projects.items():
            parts, rows = [], [r for r, _ in eps]
            for n, (r, label) in enumerate(eps, 1):
                focus = label.split(":", 1)[1].strip() if ":" in label else label
                qs = [re.sub(r"^\d+\.\s*", "", q).strip() for q in _cell(ws, r, 1).split("\n") if q.strip()]
                sec = [f"## Episode {n}\n\n**Focus:** {focus}"]
                if qs:
                    sec.append("**Interview questions this episode answers:**\n\n" + "\n".join(f"- {q}" for q in qs))
                for name in ("Situation", "Task", "Action", "Result", "Learnings"):
                    col = next((c for k, c in cols.items() if k.strip() == name), None)
                    v = _cell(ws, r, col) if col else ""
                    if v:
                        sec.append(f"### {name}\n\n{_paras(v)}")
                parts.append("\n\n".join(sec))
            docs.append(Doc(f"xlsx:historias:{slug(title)}", title, "Stories", "project", "\n\n".join(parts), s,
                            f'sheet "Historias", rows {rows[0]}-{rows[-1]}', "pt", ["fit", "project-story"],
                            related=["STARL Story Building Blocks", cur["questions_to_stories_title"]]))

    # --- networking questions per firm (Perguntas)
    if "Perguntas" in wb.sheetnames:
        ws = wb["Perguntas"]
        h = _find_row(ws, "A", "Companhia")
        firms: dict[str, list] = {}
        firm = None
        for r in range(h + 1, ws.max_row + 1):
            a, b, c = _cell(ws, r, 1), _cell(ws, r, 2), _cell(ws, r, 3)
            firm = a or firm
            if firm and c:
                firms.setdefault(firm, []).append((r, b or "General", c))
        for firm, items in firms.items():
            name = firm if len(firm) <= 3 else firm.title()
            title = cur["questions_to_ask_title"].format(firm=name)
            body = "\n\n".join(f"## {who}\n\n" + "\n".join(f"- {l.strip()}" for l in q.split("\n") if l.strip()) for _, who, q in items)
            docs.append(Doc(f"xlsx:perguntas:{slug(name)}", title, "Firm Motivation", "questions", body, s,
                            f'sheet "Perguntas", rows {items[0][0]}-{items[-1][0]}', "pt", ["fit", "networking"], related=[f"Why {name}"]))

    # --- question bank
    if "Question Bank" in wb.sheetnames:
        ws = wb["Question Bank"]
        groups, cur_g = {}, None
        for r in range(1, ws.max_row + 1):
            t = _cell(ws, r, 1)
            if not t:
                continue
            rule = _match(cur["question_bank"], t) if (t.endswith(":") or len(t.split()) <= 3) else None
            if rule:
                cur_g = rule; groups[cur_g["title"]] = {"rows": [r], "qs": []}
            elif cur_g:
                groups[cur_g["title"]]["qs"].append(t); groups[cur_g["title"]]["rows"].append(r)
        for title, g in groups.items():
            docs.append(Doc(f"xlsx:question-bank:{slug(title)}", title, "Questions", "questions",
                            "\n".join(f"- {q}" for q in g["qs"]), s, f'sheet "Question Bank", rows {g["rows"][0]}-{g["rows"][-1]}',
                            "en", ["fit", "question-bank"], related=[cur["questions_to_stories_title"]]))

    # --- question -> story map
    if "Perguntas x Historias" in wb.sheetnames:
        ws = wb["Perguntas x Historias"]
        parts, n = [], 0
        for r in range(3, ws.max_row + 1):
            q = _cell(ws, r, 1)
            if not q:
                continue
            items = [_cell(ws, r, c) for c in range(2, ws.max_column + 1) if _cell(ws, r, c)]
            lines = []
            for it in items:
                pt = project_title(cur, it)
                focus = it.split(":", 1)[1].strip() if ":" in it else ""
                lines.append(f"- {'[[' + pt + ']]' if pt else it.split(':')[0]}" + (f" — {focus}" if focus else ""))
            parts.append(f"## {q}\n\n" + ("\n".join(lines) if lines else "No story assigned yet."))
        docs.append(Doc("xlsx:perguntas-x-historias:map", cur["questions_to_stories_title"], "Questions", "map", "\n\n".join(parts), s,
                        'sheet "Perguntas x Historias"', "en", ["fit", "mapping"], related=["General FIT Questions", "Competency Based Questions"]))
    for d in docs:
        d.source_ref = d.source_ref.replace('"', "'")
    return docs


# ---------------------------------------------------------------- Markdown / text (generic, e.g. a new note dropped in)
def read_text(path: Path, src: SourceInfo, cur: dict) -> list[Doc]:
    raw = path.read_text(errors="ignore")
    fm = {}
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1); fm[k.strip()] = v.strip()
        raw = raw[m.end():]
    h1 = re.match(r"^\s*#\s+(.+)", raw)
    title = title_case(fm.get("title") or (h1.group(1) if h1 else clean_stem(path.stem)))
    body = re.sub(r"^\s*#\s+.+\n", "", raw, count=1).strip()
    folder = fm.get("folder") or "Interview Basics"
    return [Doc(f"text:{re.sub(r'[^a-z0-9]+', '-', path.stem.lower())}", title, folder, "concept", body, src.title,
                "whole file", _lang(body), ["fit"])]


READERS = {".pptx": read_pptx, ".xlsx": read_xlsx, ".md": read_text, ".txt": read_text}


def read_source(path: Path):
    cur = curation()
    if path.suffix.lower() not in READERS:
        raise ValueError(f"Unsupported source type {path.suffix!r}. Supported: {', '.join(READERS)}")
    src = source_info(path, cur)
    return src, READERS[path.suffix.lower()](path, src, cur)
