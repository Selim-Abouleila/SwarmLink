import io
import json

import pytest

from swarmlink_data.export import parquet_bytes, write_export
from swarmlink_data.simulator import Demo


@pytest.mark.parametrize("robots", [4, 20])
def test_demo_produces_valid_distinct_fleet_events(store, robots):
    demo = Demo(store, robots)
    for _ in range(5):
        demo.step()
    snapshot = store.snapshot(demo.run_id)
    assert len(snapshot["robots"]) == robots
    assert all(r["status"] == "online" for r in snapshot["robots"])
    assert snapshot["pipeline"]["rejected"] == 0
    assert snapshot["protocol"]["delivery_pct"] == 100
    assert snapshot["pipeline"]["sequence_gaps"] == 0
    assert snapshot["runs"][0]["protocol"] == "fixture-only"


def test_demo_pause_exposes_staleness_and_dropout_leaves_gaps(store):
    demo = Demo(store)
    demo.step()
    demo.paused = True
    demo.step()
    assert demo.tick == 1
    assert all(r["status"] == "stale" for r in store.snapshot(now=store.snapshot()["server_time"] + 7)["robots"])
    demo.paused = False
    demo.interrupt()
    for _ in range(3):
        demo.step()
    demo.fault_until = 0
    demo.step()
    snapshot = store.snapshot()
    assert snapshot["pipeline"]["sequence_gaps"] == 3
    assert snapshot["protocol"]["injected_faults"] == 1
    assert snapshot["protocol"]["detected_faults"] == 0  # Never infer a detector from a timeout.
    assert snapshot["protocol"]["recovery_mean_ms"] == pytest.approx(60)


def test_parquet_preserves_ids_nulls_and_scan_angles(store):
    pq = pytest.importorskip("pyarrow.parquet")
    demo = Demo(store)
    demo.step()
    events = store.events(demo.run_id)
    table = pq.read_table(io.BytesIO(parquet_bytes(events)))
    assert table.num_rows == len(events)
    assert set(table.column("event_id").to_pylist()) == {e["event_id"] for e in events}
    telemetry = next(row for row in table.to_pylist() if row["kind"] == "telemetry")
    scan = json.loads(telemetry["payload_json"])["scan"]
    assert scan["ranges_m"][0] is None
    assert scan["original_count"] == 360
    assert scan["angle_increment_rad"] > 0


def test_export_is_repeatable_and_replayable(store, tmp_path):
    demo = Demo(store); demo.step()
    output = tmp_path / "export"
    first = write_export(store, demo.run_id, output, parquet=False)
    second = write_export(store, demo.run_id, output, parquet=False)
    assert first == second
    events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
    assert len(events) == first["event_count"]
    assert all(r["status"] == "duplicate" for r in store.ingest_batch(events))
    assert json.loads((output / "metrics.json").read_text()) == store.metrics(demo.run_id)
