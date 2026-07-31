"""Small dependency-free k-means for the gap-report job (§10.5).

Deterministic: seeded farthest-point initialisation, fixed iteration cap.
Good enough for weekly clustering of a few thousand questions; swap for
scikit-learn if volumes grow.
"""

from __future__ import annotations

import math

Vector = list[float]


def _dist2(a: Vector, b: Vector) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def _mean(vectors: list[Vector]) -> Vector:
    n = len(vectors)
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / n for i in range(dim)]


def kmeans(vectors: list[Vector], k: int, max_iters: int = 25) -> list[int]:
    """Returns a cluster label per input vector."""
    if not vectors:
        return []
    k = min(k, len(vectors))

    # farthest-point init from vector 0 (deterministic)
    centroids = [vectors[0]]
    while len(centroids) < k:
        farthest = max(vectors, key=lambda v: min(_dist2(v, c) for c in centroids))
        centroids.append(farthest)

    def nearest(v: Vector) -> int:
        return min(range(k), key=lambda ci: _dist2(v, centroids[ci]))

    labels = [-1] * len(vectors)
    for _iteration in range(max_iters):
        new_labels = [nearest(v) for v in vectors]
        if new_labels == labels:
            break
        labels = new_labels
        for ci in range(k):
            members = [v for v, label in zip(vectors, labels, strict=True) if label == ci]
            if members:
                centroids[ci] = _mean(members)
    return labels


def cosine(a: Vector, b: Vector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
