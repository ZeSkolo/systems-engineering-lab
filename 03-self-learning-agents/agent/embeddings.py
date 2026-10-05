"""Deterministic hashed n-gram embeddings (no API required)."""
from __future__ import annotations

import hashlib
import math
import os
from typing import Sequence

DIM = 128


def _hash_to_index(gram: str, dim: int) -> tuple[int, float]:
    digest = hashlib.sha256(gram.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:4], "little") % dim
    sign = 1.0 if digest[4] & 1 else -1.0
    return idx, sign


def embed(text: str, dim: int = DIM) -> list[float]:
    backend = os.environ.get("EMBEDDING_BACKEND", "hash")
    if backend == "openai" and os.environ.get("OPENAI_API_KEY"):
        try:
            return _openai_embed(text, dim)
        except Exception:
            pass
    vec = [0.0] * dim
    t = f" {text.lower().strip()} "
    for n in (2, 3, 4):
        for i in range(max(0, len(t) - n + 1)):
            idx, sign = _hash_to_index(t[i : i + n], dim)
            vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


def _openai_embed(text: str, dim: int) -> list[float]:
    import json
    import urllib.request

    key = os.environ["OPENAI_API_KEY"]
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps({"model": "text-embedding-3-small", "input": text}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    vec = data["data"][0]["embedding"]
    if len(vec) >= dim:
        return vec[:dim]
    return vec + [0.0] * (dim - len(vec))
