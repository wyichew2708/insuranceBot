from harness import Candidate, Trace, TraceStore


def test_stage_timing_is_recorded() -> None:
    t = Trace()
    with t.stage("filter", admitted=3):
        pass
    assert t.stages[0].name == "filter"
    assert t.stages[0].detail["admitted"] == 3
    assert t.total_ms >= 0


def test_rejected_candidates_are_queryable() -> None:
    t = Trace()
    t.candidates = [
        Candidate(page_id="a", admitted=True),
        Candidate(page_id="b", admitted=False, reason="outside effective window"),
    ]
    assert [c.page_id for c in t.rejected] == ["b"]


def test_store_is_a_ring_buffer() -> None:
    store = TraceStore(capacity=2)
    first = Trace()
    store.put(first)
    store.put(Trace())
    store.put(Trace())
    assert len(store.all()) == 2
    assert store.get(first.trace_id) is None
