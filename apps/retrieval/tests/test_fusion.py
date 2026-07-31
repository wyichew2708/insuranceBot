from retrieval.search import rrf_fuse, sparse_overlap_score


def test_rrf_prefers_agreement() -> None:
    fused = rrf_fuse([["a", "b", "c"], ["b", "a", "d"]])
    ids = [cid for cid, _ in fused]
    assert set(ids[:2]) == {"a", "b"}
    assert ids.index("d") > ids.index("c") or ids.index("c") > 1


def test_rrf_deterministic_tie_break() -> None:
    assert rrf_fuse([["x"], ["y"]]) == rrf_fuse([["x"], ["y"]])


def test_sparse_overlap() -> None:
    q = {"claim": 0.8, "travel": 0.5}
    assert sparse_overlap_score(q, {"claim": 0.9}) > sparse_overlap_score(q, {"golf": 1.0})
    assert sparse_overlap_score(q, {}) == 0.0
    assert sparse_overlap_score({}, {"claim": 1.0}) == 0.0
