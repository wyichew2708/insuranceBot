"""Bundle ids must sort chronologically — keep-last-N and rollback depend on it."""

import time

from ingestion.pipeline import new_bundle_id


def test_bundle_ids_are_lexically_chronological() -> None:
    first = new_bundle_id()
    time.sleep(0.002)  # id resolution is microseconds
    second = new_bundle_id()
    assert first < second
    assert sorted([second, first]) == [first, second]


def test_bundle_id_shape() -> None:
    bundle_id = new_bundle_id()
    stamp, _, suffix = bundle_id.partition("-")
    assert len(stamp) == 20 and stamp.isdigit()  # YYYYMMDDHHMMSSffffff
    assert len(suffix) == 6
