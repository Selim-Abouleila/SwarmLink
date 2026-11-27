import copy
import json
from types import SimpleNamespace as NS

import pytest

from agents.ros2_agent import scan_payload, battery_percent
from swarmlink_data.contracts import Scan
from swarmlink_data.edge import Outbox


def test_outbox_survives_restart_and_accepts_retry_ack(tmp_path, event):
    path = tmp_path / "outbox.sqlite"
    outbox = Outbox(path)
    raw = event()
    outbox.append(raw)
    outbox.append(raw)
    outbox.close()
    restarted = Outbox(path)
    assert restarted.size() == 1
    batch = restarted.batch()
    assert batch[0][1] == raw
    restarted.acknowledge(batch, [{"event_id": raw["event_id"], "status": "duplicate"}])
    assert restarted.size() == 0
    restarted.close()


def test_partial_or_mismatched_ack_preserves_entire_batch(tmp_path, event):
    outbox = Outbox(tmp_path / "outbox.sqlite")
    outbox.append(event()); outbox.append(event())
    batch = outbox.batch()
    good = {"event_id": batch[0][1]["event_id"], "status": "accepted"}
    for acknowledgements in ([good], [good, {"event_id": "wrong", "status": "accepted"}], [good, None]):
        with pytest.raises(ValueError):
            outbox.acknowledge(batch, acknowledgements)
        assert outbox.size() == 2
    outbox.close()


def test_rejected_event_moves_to_edge_quarantine(tmp_path, event, store):
    outbox = Outbox(tmp_path / "outbox.sqlite")
    invalid = event(payload={"battery_pct": 101})
    outbox.append(invalid)
    batch = outbox.batch()
    outbox.acknowledge(batch, store.ingest_batch([e for _, e in batch]))
    assert outbox.size() == 0
    assert outbox.db.execute("SELECT event_id FROM rejected").fetchone()[0] == invalid["event_id"]
    outbox.close()


def test_full_outbox_never_overwrites_unacknowledged_data(tmp_path, event):
    outbox = Outbox(tmp_path / "outbox.sqlite", max_pending=1)
    raw = event()
    outbox.append(raw)
    with pytest.raises(BufferError):
        outbox.append(event())
    altered = copy.deepcopy(raw); altered["payload"]["battery_pct"] = 1
    with pytest.raises(ValueError):
        outbox.append(altered)
    assert outbox.batch()[0][1] == raw
    outbox.close()


def test_lidar_downsample_preserves_angle_and_invalid_returns():
    msg = NS(ranges=[1, 2, float("inf"), 3, .01, 3, 2, 3, float("nan"), 3], angle_min=-1,
             angle_increment=.2, range_min=.1, range_max=5, header=NS(stamp=NS(sec=7, nanosec=500000000)))
    payload = scan_payload(msg, 5)
    assert payload["ranges_m"] == [1, None, None, 2, None]
    assert payload["angle_increment_rad"] == pytest.approx(.4)
    assert payload["original_count"] == 10
    assert payload["stamp_s"] == 7.5
    Scan.model_validate(payload)


@pytest.mark.parametrize("value,scale,expected", [
    (.9444, "fraction", 94.44), (94.44, "percent", 94.44),
    (.5, "percent", .5), (94.44, "fraction", None),
    (-1, "percent", None), (float("nan"), "percent", None),
])
def test_battery_scale_is_explicit_and_preserves_unknowns(value, scale, expected):
    result = battery_percent(value, scale)
    assert result is None if expected is None else result == pytest.approx(expected)


def test_large_scans_split_batches_below_http_size_limit(tmp_path, event):
    from swarmlink_data.edge import MAX_BATCH_BYTES
    outbox = Outbox(tmp_path / "outbox.sqlite")
    for _ in range(50):
        outbox.append(event(payload={"scan": {"ranges_m": [1.123456789012345] * 1440}}))
    batch = outbox.batch()
    assert 1 < len(batch) < 50
    wire = json.dumps({"events": [e for _, e in batch]}, separators=(",", ":")).encode()
    assert len(wire) <= MAX_BATCH_BYTES < 1024 * 1024
    assert outbox.size() == 50
    outbox.close()


def test_protocol_example_emits_separate_valid_session(tmp_path, store):
    from examples.protocol_adapter import ProtocolEvents
    outbox = Outbox(tmp_path / "protocol.sqlite")
    trace = ProtocolEvents(outbox, "robot-01", "protocol-test", "example-protocol")
    trace.emit("message_sent", {"message_id": "m1", "recipients": ["robot-02"], "message_type": "heartbeat", "size_bytes": 20})
    trace.emit("decision", {"decision_id": "d1", "value": "ready"})
    events = [e for _, e in outbox.batch()]
    assert [e["sequence"] for e in events] == [0, 1]
    assert all(r["status"] == "accepted" for r in store.ingest_batch(events))
    assert store.metrics("protocol-test")["expected_deliveries"] == 1
    outbox.close()
