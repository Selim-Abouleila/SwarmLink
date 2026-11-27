"""Dashboard restarts start empty; the durable archive remains usable offline."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from swarmlink_data.app import create_app
from swarmlink_data.store import Store


def test_fresh_view_clears_history_counts_and_old_retries(tmp_path, event):
    old = event(sequence=0)
    store = Store(tmp_path)
    store.ingest_batch([old, old, event(sequence=3, session_id=old["session_id"]), "broken JSON"])
    assert store.snapshot()["pipeline"]["sequence_gaps"] == 2
    store.close()

    store = Store(tmp_path, expected_robots=1, fresh_view=True)
    try:
        snapshot = store.snapshot("test-run")
        assert snapshot["collection"]["fresh_view"] is True
        assert snapshot["run_id"] is None
        assert snapshot["runs"] == snapshot["recent_events"] == snapshot["quarantine"] == []
        assert snapshot["robots"][0]["payload"] == {}
        assert snapshot["robots"][0]["history"] == []
        assert snapshot["robots"][0]["status"] == "unseen"
        for key in ("accepted", "duplicate", "rejected", "events_per_s", "sequence_gaps", "future_timestamps"):
            assert snapshot["pipeline"][key] == 0
        assert snapshot["pipeline"]["p95_event_age_ms"] is None
        assert store.events("test-run") == []

        # A retry from the previous launch must not restore its old measurements.
        assert store.ingest(old)["status"] == "duplicate"
        assert store.snapshot()["robots"][0]["payload"] == {}
        assert store.snapshot()["pipeline"]["accepted"] == 0

        new = event(sequence=100, session_id=old["session_id"], payload={"battery_pct": 70})
        store.ingest(new)
        snapshot = store.snapshot()
        assert snapshot["pipeline"]["accepted"] == 1
        assert snapshot["pipeline"]["duplicate"] == 1
        assert snapshot["pipeline"]["sequence_gaps"] == 0
        assert snapshot["pipeline"]["events_per_s"] == .1
        assert [h["battery_pct"] for h in snapshot["robots"][0]["history"]] == [70]
        assert [e["event_id"] for e in store.events("test-run")] == [new["event_id"]]
    finally:
        store.close()

    archive = Store(tmp_path)
    try:
        assert len(archive.events("test-run")) == 3
        assert archive.snapshot()["pipeline"]["rejected"] == 1
    finally:
        archive.close()


def test_restart_excludes_recovered_archive_and_does_not_trust_robot_clock(tmp_path, event):
    store = Store(tmp_path)
    store.ingest(event())
    # A crash left a fsynced journal entry without its SQLite transaction.
    with store.db:
        for table in ("events", "receipts", "runs"):
            store.db.execute(f"DELETE FROM {table}")
    store.close()

    store = Store(tmp_path, fresh_view=True)
    try:
        assert store.recovered_receipts == 1
        assert store.events("test-run") == []
        assert store.snapshot()["pipeline"]["accepted"] == 0
        # First-time delivery of an older queued event is still kept, but marked stale.
        queued = event(observed_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat())
        store.ingest(queued)
        assert store.snapshot()["robots"][0]["status"] == "stale"
        assert store.events("test-run")[0]["event_id"] == queued["event_id"]
    finally:
        store.close()


def test_protocol_metrics_are_limited_to_the_current_collection(tmp_path, event):
    payload = {"message_id": "old-message", "message_type": "heartbeat", "recipients": ["robot-02"], "size_bytes": 10}
    store = Store(tmp_path)
    assert store.ingest(event(source="protocol", kind="message_sent", payload=payload))["status"] == "accepted"
    assert store.metrics("test-run")["transmissions"] == 1
    store.close()

    store = Store(tmp_path, fresh_view=True)
    try:
        assert store.metrics("test-run")["transmissions"] == 0
        assert store.ingest(event(source="protocol", kind="message_sent", payload={**payload, "message_id": "new-message"}))["status"] == "accepted"
        assert store.metrics("test-run")["transmissions"] == 1
        assert store.metrics("test-run")["bytes_sent"] == 10
    finally:
        store.close()


def test_app_restart_resets_api_and_new_paired_robot_appears(tmp_path, event):
    keys = {"robot-01": "test-robot-one-credential", "robot-02": "test-robot-two-credential"}

    def send(client, raw):
        robot_id = raw["robot_id"]
        return client.post("/api/events", json=raw,
                           headers={"X-Robot-ID": robot_id, "Authorization": "Bearer " + keys[robot_id]})

    old = event(run_id="previous-run")
    with TestClient(create_app(tmp_path, robots=1, keys=keys)) as client:
        assert send(client, old).json()["results"][0]["status"] == "accepted"
        original_id = client.get("/api/snapshot").json()["collection"]["id"]
        # Refreshing the page / reading the API does not start a new collection.
        assert client.get("/api/snapshot").json()["collection"]["id"] == original_id

    with TestClient(create_app(tmp_path, robots=1, keys=keys)) as client:
        snapshot = client.get("/api/snapshot?run_id=previous-run").json()
        assert snapshot["collection"]["id"] != original_id
        assert snapshot["pipeline"]["accepted"] == 0
        assert client.get("/api/compare").json() == []
        assert client.get("/api/runs/previous-run/export").status_code == 404
        assert send(client, old).json()["results"][0]["status"] == "duplicate"
        with client.websocket_connect("/api/stream?run_id=previous-run") as socket:
            assert socket.receive_json()["robots"][0]["status"] == "unseen"

        send(client, event())
        second_robot = event(robot_id="robot-02")
        assert send(client, second_robot).json()["results"][0]["status"] == "accepted"
        snapshot = client.get("/api/snapshot?run_id=previous-run").json()
        assert snapshot["run_id"] == "test-run"
        assert snapshot["pipeline"]["accepted"] == 2
        assert {r["robot_id"] for r in snapshot["robots"] if r["status"] == "online"} == set(keys)
        assert [r["run_id"] for r in client.get("/api/compare").json()] == ["test-run"]
        csv = client.get("/api/runs/test-run/export").text
        assert second_robot["event_id"] in csv and old["event_id"] not in csv
