"""Command-line front end: parses commands, prints results, shows errors plainly. Logic lives in harness.py."""
import argparse
import sys
import textwrap

from .harness import HELP, Harness
from .llm import LLMError


def _print_ask(r):
    print(r.answer)
    if r.citations:
        print("\nSources:")
        for c in r.citations:
            print(f"  [{c['n']}] {c['path']} — {c['heading']}  ({c['source_ref']})")
    tag = f"[{r.status}{' · ' + r.note if r.note else ''}]"
    s = r.stats
    print(f"\n{tag}" + (f" {s.get('pipeline_s', '?')}s total, {s.get('generated_tokens', 0)} tokens" if s else ""))


def _print_search(hits):
    if not hits:
        print("No passages indexed. Run: ingest <file>")
    for h in hits:
        print(f"#{h.rank}  {h.path}  —  {h.heading}   (cosine {h.vec_score:.2f}, keyword {h.kw_score:.1f})")
        print(textwrap.indent(h.text if len(h.text) < 700 else h.text[:700] + " …", "    "))
        print()


def chat_loop(h: Harness):
    print("Chat with Milo. Wiki notes are used automatically when relevant.\n/ask <q>, /search <q>, /notes on|off, /reset, /help, /quit\n")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not line:
            continue
        try:
            if line in ("/quit", "/exit"):
                break
            elif line == "/reset":
                h.reset_chat(); print("(conversation cleared)")
            elif line in ("/notes on", "/notes off"):
                h.use_notes = line.endswith("on"); print(f"(wiki lookup in chat: {'on' if h.use_notes else 'off'})")
            elif line == "/help":
                print(HELP)
            elif line.startswith("/ask "):
                _print_ask(h.ask(line[5:]))          # separate pipeline: not added to chat history
            elif line.startswith("/search "):
                _print_search(h.search(line[8:]))
            else:
                print("milo> ", end="", flush=True)
                r = h.chat(line, on_token=lambda t: print(t, end="", flush=True))
                used = r.stats.get("notes_used")
                print(f"\n      ({r.stats['total_s']}s" + (f"; used notes: {', '.join(used)}" if used else "; no notes used") + ")")
        except LLMError as e:
            print(f"\n[error] {e}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="fitwiki", add_help=False)
    p.add_argument("command", nargs="?", default="help", choices=["chat", "ask", "search", "ingest", "status", "help"])
    p.add_argument("arg", nargs="*")
    a = p.parse_args(argv)
    h = Harness()
    try:
        if a.command == "help":
            print(HELP)
        elif a.command == "chat":
            chat_loop(h)
        elif a.command == "ask":
            _print_ask(h.ask(" ".join(a.arg)))
        elif a.command == "search":
            _print_search(h.search(" ".join(a.arg)))
        elif a.command == "ingest":
            r = h.ingest(" ".join(a.arg))
            print(f"Ingested {r['source']}: {r['notes_from_source']} notes. Vault: {len(r['written'])} written, {r['unchanged']} unchanged, "
                  f"{len(r['archived'])} archived, {len(r['skipped_edited'])} skipped (hand-edited). Index: {r['index']}")
        elif a.command == "status":
            print(h.status())
    except (LLMError, ValueError, FileNotFoundError) as e:
        print(f"[error] {e}", file=sys.stderr); return 1
    return 0
