import copy
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from swarmlink_data.contracts import Event
from swarmlink_data.store import Store


@pytest.mark.parametrize("patch", [
    {"schema_version": 2}, {"sequence": -1}, {"sequence": 1.5}, {"robot_id": "../robot"},
    {"observed_at": "2026-01-01T00:00:00"}, {"payload": {"battery_pct": 101}},
    {"payload": {"battery_pct": float("nan")}}, {"payload": {"mystery": 1}},
    {"source": "simulation"}, {"extra_field": "not silently ignored"},
])
def test_rejects_invalid_events_without_losing_original(store, event, patch):
    raw = event(**patch)
    result = store.ingest(raw)
    assert result["status"] == "rejected"
    assert result["event_id"] == raw["event_id"] or "NaN" in result["reason"]
    assert store.events("test-run") == []
    journal = list(store.raw_dir.glob("date=*/receipts.jsonl"))[0]
    receipt = json.loads(journal.read_text().splitlines()[0])
    assert json.loads(receipt["raw_json"])["event_id"] == raw["event_id"]


def test_missing_sensor_data_remains_null(event):
    model = Event.model_validate(event(payload={}))
    assert model.payload["battery_pct"] is None
    assert model.payload["scan"] is None
    assert model.payload["pose"] is None


def test_retry_idempotence_and_content_collision(store, event):
    raw = event()
    assert store.ingest(raw)["status"] == "accepted"
    assert store.ingest(copy.deepcopy(raw))["status"] == "duplicate"
    changed = copy.deepcopy(raw)
    changed["payload"]["battery_pct"] = 25
    assert store.ingest(changed)["status"] == "rejected"
    assert len(store.events("test-run")) == 1
    counts = store.snapshot()["pipeline"]
    assert (counts["accepted"], counts["duplicate"], counts["rejected"]) == (1, 1, 1)


def test_sequence_gaps_close_on_late_delivery_and_latest_does_not_regress(store, event):
    now = datetime.now(timezone.utc)
    first = event(sequence=0, observed_at=(now - timedelta(seconds=2)).isoformat())
    third = event(sequence=2, session_id=first["session_id"], observed_at=now.isoformat(), payload={"battery_pct": 70})
    second = event(sequence=1, session_id=first["session_id"], observed_at=(now - timedelta(seconds=1)).isoformat(), payload={"battery_pct": 75})
    store.ingest_batch([first, third])
    assert store.snapshot()["pipeline"]["sequence_gaps"] == 1
    store.ingest(second)
    snapshot = store.snapshot()
    assert snapshot["pipeline"]["sequence_gaps"] == 0
    assert snapshot["robots"][0]["payload"]["battery_pct"] == 70
    assert [p["battery_pct"] for p in snapshot["robots"][0]["history"]] == [80, 75, 70]
    collision = event(sequence=1, session_id=first["session_id"])
    assert store.ingest(collision)["status"] == "rejected"


def test_robot_restart_can_reset_sequence_with_new_session(store, event):
    assert [r["status"] for r in store.ingest_batch([event(), event()])] == ["accepted", "accepted"]


def test_old_buffered_events_are_not_marked_live(store, event):
    old = event(observed_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    store.ingest(old)
    robot = store.snapshot()["robots"][0]
    assert robot["status"] == "stale"
    assert robot["receipt_age_s"] < 1
    assert robot["age_s"] > 59
    assert len(store.snapshot()["robots"]) == 4


def test_clock_skew_is_visible(store, event):
    store.ingest(event(observed_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()))
    assert store.snapshot()["robots"][0]["clock_skew"]
    assert store.snapshot()["pipeline"]["p95_event_age_ms"] is None
    assert store.snapshot()["pipeline"]["future_timestamps"] == 1


def test_run_metadata_cannot_mix_experiments(store, event):
    store.ingest(event())
    assert store.ingest(event(protocol="different"))["status"] == "rejected"
    assert store.ingest(event(source="simulation", sim_time_s=0))["status"] == "rejected"
    assert store.ingest(event(run_id="different-run", source="simulation", sim_time_s=0))["status"] == "accepted"


def test_recovers_fsynced_journal_after_uncommitted_sql_and_torn_tail(tmp_path, event):
    directory = tmp_path / "data"
    store = Store(directory)
    raw = event()
    store.ingest(raw)
    journal = list(store.raw_dir.glob("date=*/receipts.jsonl"))[0]
    # Emulate the journal commit surviving while SQLite's transaction did not.
    with store.db:
        store.db.execute("DELETE FROM events")
        store.db.execute("DELETE FROM receipts")
        store.db.execute("DELETE FROM runs")
    store.close()
    complete = journal.read_bytes()
    with journal.open("ab") as stream:
        stream.write(b'{"receipt_id":"torn')
    recovered = Store(directory)
    assert recovered.recovered_receipts == 1
    assert recovered.events("test-run")[0]["event_id"] == raw["event_id"]
    assert journal.read_bytes() == complete
    recovered.close()
    again = Store(directory)
    assert again.recovered_receipts == 0
    assert len(again.events("test-run")) == 1
    again.close()


def test_complete_corrupt_journal_is_not_silently_deleted(tmp_path):
    directory = tmp_path / "data"
    journal = directory / "bronze/date=2026-01-01/receipts.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_bytes(b"broken complete record\n")
    with pytest.raises(RuntimeError, match="Corrupted complete"):
        Store(directory)
    assert journal.read_bytes() == b"broken complete record\n"


def test_rejects_second_writer_until_first_closes(tmp_path):
    first = Store(tmp_path)
    with pytest.raises(RuntimeError, match="already in use"):
        Store(tmp_path)
    first.close()
    second = Store(tmp_path)
    second.close()


def test_malformed_json_is_quarantined(store):
    result = store.ingest('{"broken":')
    assert result["status"] == "rejected"
    assert store.snapshot()["pipeline"]["rejected"] == 1
