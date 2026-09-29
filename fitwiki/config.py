"""Central settings. Everything is local: the only network endpoint allowed is loopback Ollama."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"          # human notes (open this folder in Obsidian)
INDEX = ROOT / ".index"         # machine state: chunks, vectors, manifest, note records (outside the vault)
SOURCES = ROOT / "sources"      # read-only copies of ingested originals
OUTPUTS = ROOT / "outputs"      # saved chat transcripts and ask answers
PROMPTS = ROOT / "prompts"      # assistant persona and research rules, kept in separate files
CURATION = ROOT / "config" / "curation.json"

OLLAMA_URL = "http://127.0.0.1:11434"
CHAT_MODEL = "gemma3n:e4b"      # Gemma 3n "E4B", Q4 as served by Ollama
EMBED_MODEL = "nomic-embed-text"
NUM_CTX = 8192                  # same everywhere so Ollama never reloads the model between modes

TOP_K = 5
CHAT_HISTORY_CHARS = 12000      # rough budget for conversation memory (~3k tokens)
EXCLUDED_FROM_RETRIEVAL = {"index.md", "Sources"}   # navigation pages are not evidence
