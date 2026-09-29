"""Thin client for the local Ollama runtime (chat + embeddings). No cloud, no fallback."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import numpy as np

from .config import CHAT_MODEL, EMBED_MODEL, NUM_CTX, OLLAMA_URL

assert urllib.parse.urlparse(OLLAMA_URL).hostname in {"127.0.0.1", "localhost"}, "local-only by design"


class LLMError(RuntimeError):
    pass


@dataclass
class ChatResult:
    text: str
    stats: dict = field(default_factory=dict)


def _open(path: str, payload: dict, timeout: int = 900):
    req = urllib.request.Request(OLLAMA_URL + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise LLMError(f"Ollama returned HTTP {e.code}: {e.read().decode(errors='ignore')[:200]}") from e
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise LLMError(f"Cannot reach local Ollama at {OLLAMA_URL}. Start it with `ollama serve`. ({e})") from e


def chat(messages: list[dict], temperature: float = 0.7, on_token=None, num_predict: int = 700) -> ChatResult:
    payload = {"model": CHAT_MODEL, "messages": messages, "stream": True,
               "options": {"temperature": temperature, "num_ctx": NUM_CTX, "num_predict": num_predict}}
    t0, first, parts, final = time.perf_counter(), None, [], {}
    with _open("/api/chat", payload) as resp:
        for line in resp:
            if not line.strip():
                continue
            obj = json.loads(line)
            if "error" in obj:
                raise LLMError(obj["error"])
            piece = obj.get("message", {}).get("content", "").replace("\u2581", " ")   # Gemma tokenizer artifact
            if piece:
                if first is None:
                    first = time.perf_counter() - t0
                parts.append(piece)
                if on_token:
                    on_token(piece)
            if obj.get("done"):
                final = obj
    total = time.perf_counter() - t0
    gen = final.get("eval_count", 0)
    gen_s = final.get("eval_duration", 0) / 1e9
    stats = {"total_s": round(total, 2), "first_token_s": round(first or total, 2),
             "prompt_tokens": final.get("prompt_eval_count", 0), "generated_tokens": gen,
             "gen_tokens_per_s": round(gen / gen_s, 1) if gen_s else None,
             "model_load_s": round(final.get("load_duration", 0) / 1e9, 2)}
    return ChatResult("".join(parts).strip(), stats)


def embed(texts: list[str], batch: int = 16) -> np.ndarray:
    out = []
    for i in range(0, len(texts), batch):
        with _open("/api/embed", {"model": EMBED_MODEL, "input": texts[i:i + batch]}) as resp:
            out.extend(json.loads(resp.read())["embeddings"])
    arr = np.array(out, dtype=np.float32)
    return arr / np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-9)


def loaded_models() -> list[dict]:
    """What Ollama currently holds in memory (used for memory measurements)."""
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/ps", timeout=5) as r:
            return json.loads(r.read()).get("models", [])
    except Exception:
        return []
