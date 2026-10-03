"""K2: cosine top-k search over canon corpus embeddings (brute-force numpy).

At ~11k vectors × 768d a brute-force matmul is ~milliseconds — no ANN index
needed. Embeddings are stored as float32 blobs in canon_kb (canon_chunk); the
search script loads them, this module ranks.
"""
from __future__ import annotations

import numpy as np


def top_k_cosine(query: list[float], matrix: list[list[float]] | np.ndarray, k: int = 5) -> list[tuple[int, float]]:
    """Return [(row_index, cosine_similarity), …] of the k closest rows to query."""
    M = np.asarray(matrix, dtype=np.float32)
    if M.ndim != 2 or M.shape[0] == 0:
        return []
    q = np.asarray(query, dtype=np.float32)
    qn = q / (np.linalg.norm(q) or 1.0)
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    sims = Mn @ qn
    idx = np.argsort(-sims)[: max(0, k)]
    return [(int(i), float(sims[i])) for i in idx]
