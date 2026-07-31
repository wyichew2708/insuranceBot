"""Deterministic pseudo-embeddings for dev/e2e when no embed endpoint is
configured. Hash-bucketed bag-of-words, L2-normalised — NOT semantically
meaningful, but stable and overlap-sensitive, which is enough to exercise
the ingestion -> retrieval path and the eval harness offline.

Production always configures VLLM_EMBED_BASE_URL; services log loudly when
falling back to this.
"""

from __future__ import annotations

import hashlib
import math
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def pseudo_embedding(text: str, dim: int = 1024) -> list[float]:
    vector = [0.0] * dim
    for token in _TOKEN_RE.findall(text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]
    return vector


def pseudo_sparse(text: str) -> dict[str, float]:
    weights: dict[str, float] = {}
    for token in _TOKEN_RE.findall(text.lower()):
        weights[token] = weights.get(token, 0.0) + 1.0
    total = sum(weights.values()) or 1.0
    return {token: count / total for token, count in weights.items()}
