"""Vault hygiene: filenames, H1 == filename, links resolve, no orphans, sources present, no ids in the visible body."""
import re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fitwiki import naming
V = ROOT / "vault"
files = list(V.rglob("*.md"))
notes = {p.stem: p for p in files}
problems, inbound = [], {k: 0 for k in notes}
if len(files) != len(notes):
    from collections import Counter
    problems += [f"duplicate filename (ambiguous links): {k}" for k, n in Counter(p.stem for p in files).items() if n > 1]
for stem, p in notes.items():
    text = p.read_text()
    if stem != "index":
        for pr in naming.problems(stem): problems.append(f"{p.relative_to(V)}: filename {pr}")
        m = re.search(r"^# (.+)$", text, re.M)
        if not m or m.group(1) != stem: problems.append(f"{p.relative_to(V)}: H1 != filename")
        fm = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not fm or "source" not in fm.group(1) and p.parent.name != "Sources": problems.append(f"{p.relative_to(V)}: no source metadata")
        body = text[fm.end():] if fm else text
        if re.search(r"\b[0-9a-f]{10,}\b|note_id|xlsx:|pptx:", body): problems.append(f"{p.relative_to(V)}: machine id in visible body")
    for target in re.findall(r"\[\[([^\]|#]+)", text):
        if target not in notes: problems.append(f"{p.relative_to(V)}: broken link [[{target}]]")
        elif target != stem: inbound[target] += 1
orph = [k for k, v in inbound.items() if v == 0 and k != "index"]
print(f"{len(files)} notes, {sum(inbound.values())} resolved links, folders: {sorted({p.parent.name for p in notes.values() if p.parent != V})}")
print("orphans (no incoming links):", orph or "none")
print("PROBLEMS:" if problems else "no problems", *problems, sep="\n  ")
sys.exit(1 if problems else 0)
