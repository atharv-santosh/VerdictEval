from aeh.events import TrajectoryEvent, content_hash, read_jsonl, write_jsonl
import pytest


def test_event_roundtrip_json():
    event = TrajectoryEvent(run_id="r1", seq=0, ts=1.0, type="run_start", payload={"a": 1})
    line = event.to_json()
    back = TrajectoryEvent.from_json(line)
    assert back == event


def test_invalid_event_type_rejected():
    with pytest.raises(ValueError):
        TrajectoryEvent(run_id="r1", seq=0, ts=1.0, type="not_a_type", payload={})


def test_content_hash_is_deterministic_and_order_independent():
    a = {"x": 1, "y": 2}
    b = {"y": 2, "x": 1}
    assert content_hash(a) == content_hash(b)


def test_content_hash_differs_on_value_change():
    assert content_hash({"seed": 1}) != content_hash({"seed": 2})


def test_write_read_jsonl_roundtrip(tmp_path):
    path = tmp_path / "traj.jsonl"
    events = [
        TrajectoryEvent(run_id="r1", seq=i, ts=float(i), type="decision", payload={"i": i})
        for i in range(5)
    ]
    write_jsonl(str(path), events)
    back = read_jsonl(str(path))
    assert back == events
