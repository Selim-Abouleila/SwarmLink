"""Reproducible, replace-in-place exports from a consistent database read."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

from .analytics import nearest_obstacle, protocol_metrics
from .store import Store, canonical

FIELDS = ["event_id", "run_id", "robot_id", "session_id", "sequence", "source", "kind", "observed_at", "received_at", "metric_time", "scenario", "protocol", "seed", "x_m", "y_m", "yaw_rad", "frame_id", "map_id", "linear_m_s", "angular_rad_s", "battery_pct", "battery_v", "nearest_obstacle_m", "payload_json"]


def flatten(event: dict) -> dict:
    row = {key: event.get(key) for key in FIELDS}
    payload = event["payload"]
    if event["kind"] == "telemetry":
        row.update(payload.get("pose") or {})
        row.update({key: payload.get(key) for key in ("linear_m_s", "angular_rad_s", "battery_pct", "battery_v")})
        row["nearest_obstacle_m"] = nearest_obstacle(payload)
    row["payload_json"] = canonical(payload)
    return row


def csv_text(events: list[dict]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    for event in events:
        row = flatten(event)
        # Prevent spreadsheet formula execution without changing the canonical JSON/Parquet data.
        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for k, v in row.items()})
    return stream.getvalue()


def parquet_bytes(events: list[dict]) -> bytes:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    numeric = {"received_at", "metric_time", "x_m", "y_m", "yaw_rad", "linear_m_s", "angular_rad_s", "battery_pct", "battery_v", "nearest_obstacle_m"}
    integers = {"sequence", "seed"}
    schema = pa.schema([(key, pa.float64() if key in numeric else pa.int64() if key in integers else pa.string()) for key in FIELDS])
    frame = pd.DataFrame([flatten(event) for event in events], columns=FIELDS)
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pandas(frame, schema=schema, preserve_index=False), sink, compression="zstd")
    return sink.getvalue().to_pybytes()


def write_export(store: Store, run_id: str, output: Path, parquet: bool = True) -> dict:
    events = store.events(run_id)
    if not events:
        raise ValueError("No events found for that run")
    # Obtain all bytes before writing, so missing optional dependencies do not leave a half export.
    products = {"events.csv": csv_text(events).encode("utf-8"),
                "events.jsonl": "".join(canonical({k: v for k, v in e.items() if k not in ("received_at", "metric_time")}) + "\n" for e in events).encode("utf-8"),
                "metrics.json": (json.dumps(protocol_metrics(events), indent=2) + "\n").encode()}
    if parquet:
        products["events.parquet"] = parquet_bytes(events)
    manifest = {"schema_version": 1, "run_id": run_id, "event_count": len(events),
                "scenario": events[0]["scenario"], "protocol": events[0]["protocol"], "seed": events[0]["seed"],
                "files": list(products), "source": "committed, deduplicated events; raw receipts remain in bronze/"}
    output.mkdir(parents=True, exist_ok=True)
    for name, data in products.items():
        temp = output / (name + ".tmp")
        temp.write_bytes(data)
        os.replace(temp, output / name)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
