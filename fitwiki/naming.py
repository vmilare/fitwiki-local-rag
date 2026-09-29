"""Note-naming rules: short descriptive titles, never hashes, export labels or full sentences."""
import re

ACRONYMS = {"FIT", "PEI", "MBA", "STARL", "TMAY", "AI", "CV", "5G", "SQL", "STAR"}
SMALL = {"a", "an", "and", "at", "for", "in", "of", "on", "or", "the", "to", "with", "how", "is"}
EXPORT_WORDS = {"export", "sheet", "slide", "row", "cell", "untitled", "copy", "final", "tmp", "output", "vf", "sheet1"}
ILLEGAL = re.compile(r'[\\/:*?"<>|#^\[\]]')


def title_case(text: str) -> str:
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    out = []
    for i, w in enumerate(words):
        bare = re.sub(r"\W", "", w)
        if bare.upper() in ACRONYMS:
            out.append(w.upper() if w.isalpha() else w)
        elif i and w.lower() in SMALL:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def problems(title: str) -> list[str]:
    """Return why a title is not acceptable (empty list = fine)."""
    bad = []
    words = title.split()
    if not title.strip():
        return ["empty"]
    if ILLEGAL.search(title):
        bad.append("illegal filename characters")
    if len(title) > 60 or len(words) > 8:
        bad.append("too long / sentence-like")
    if title.rstrip()[-1] in ".!?,;:":
        bad.append("ends with punctuation (sentence-like)")
    if re.search(r"\b[0-9a-f]{8,}\b", title, re.I) or re.search(r"\d{6,}", title):
        bad.append("looks like a hash or export id")
    if {w.lower() for w in words} & EXPORT_WORDS:
        bad.append("export-style word")
    if re.match(r"^\d+[\s._-]", title):
        bad.append("starts with a counter")
    return bad


def clean_stem(stem: str) -> str:
    """Turn an export-style file stem into a readable title (fallback when no curated title exists)."""
    s = re.sub(r"[_&]+", " ", stem)
    s = re.sub(r"\b(v\d+|vf|\d{6,8}|final|copy)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return title_case(s)


def heuristic_title(question: str) -> str:
    """Fallback: 'Why Change Consulting Firm? / What are ...' -> 'Why Change Consulting Firm'."""
    first = re.split(r"\s+/\s+", question)[0]
    first = re.sub(r"[?!.:]+$", "", first.strip())
    first = re.sub(r"\([^)]*\)", "", first)
    return title_case(" ".join(first.split()[:6]))
