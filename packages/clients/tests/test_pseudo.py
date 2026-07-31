from insurance_clients.pseudo import pseudo_embedding, pseudo_sparse


def test_deterministic() -> None:
    assert pseudo_embedding("travel claim") == pseudo_embedding("travel claim")


def test_normalised_and_dimensioned() -> None:
    vec = pseudo_embedding("some words here", dim=256)
    assert len(vec) == 256
    assert abs(sum(v * v for v in vec) - 1.0) < 1e-9


def test_overlap_scores_higher_than_disjoint() -> None:
    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    query = pseudo_embedding("travel claim procedure")
    close = pseudo_embedding("how to submit a travel claim")
    far = pseudo_embedding("marine cargo institute clauses")
    assert cos(query, close) > cos(query, far)


def test_sparse_weights_normalised() -> None:
    weights = pseudo_sparse("claim claim travel")
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert weights["claim"] > weights["travel"]
