"""K2: cosine top-k search over canon corpus embeddings."""
from app.services.canon_vectors import top_k_cosine


def test_identical_vector_ranks_first():
    M = [[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]]
    res = top_k_cosine([1.0, 0.0], M, k=2)
    assert res[0][0] == 0
    assert res[0][1] > res[1][1]


def test_orthogonal_is_lowest():
    M = [[1.0, 0.0], [0.0, 1.0]]
    res = top_k_cosine([1.0, 0.0], M, k=2)
    assert res[0][0] == 0 and res[1][0] == 1


def test_k_limits_results():
    M = [[1.0, 0.0]] * 5
    assert len(top_k_cosine([1.0, 0.0], M, k=3)) == 3


def test_magnitude_invariant():
    # cosine ignores magnitude
    M = [[2.0, 0.0], [0.0, 5.0]]
    res = top_k_cosine([10.0, 0.0], M, k=1)
    assert res[0][0] == 0
