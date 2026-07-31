from analytics.kmeans import cosine, kmeans


def test_two_obvious_clusters() -> None:
    vectors = [[0.0, 0.1], [0.1, 0.0], [5.0, 5.1], [5.1, 5.0]]
    labels = kmeans(vectors, k=2)
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]


def test_k_larger_than_n_is_clamped() -> None:
    labels = kmeans([[1.0], [2.0]], k=10)
    assert len(labels) == 2


def test_empty_input() -> None:
    assert kmeans([], k=3) == []


def test_deterministic() -> None:
    vectors = [[float(i % 3), float(i % 5)] for i in range(20)]
    assert kmeans(vectors, 3) == kmeans(vectors, 3)


def test_cosine() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
