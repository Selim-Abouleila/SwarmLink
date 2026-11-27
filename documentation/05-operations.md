# Document 05 - Operations and reliability boundaries

Run the commands in this guide from `data-engineering/`.

## Local data products

`--data` selects one collector-owned directory. Defaults are `.runtime/demo` and `.runtime/live`; keep simulation and physical experiments separate.

| Layer | Location | Purpose |
| --- | --- | --- |
| Bronze | `bronze/date=YYYY-MM-DD/receipts.jsonl` | UTC-partitioned, append-only receipt envelopes, including rejected schema and duplicate payloads |
| Silver | `fleet.sqlite` (`events`, `runs`) | Committed, validated, deduplicated events and immutable run metadata |
| Quality | `fleet.sqlite` (`receipts`) | Accepted/duplicate/rejected status, reason, original submitted JSON and collection time |
| Portable silver | Export `events.parquet`, `events.csv`, `events.jsonl` | Explicit columns plus full retained payload for downstream analysis |
| Gold | Export `metrics.json` | Same per-run delivery/detection/recovery functions used by the dashboard |

Each journal line contains a new `receipt_id`, wall-clock `received_at`, and `raw_json` string. That string holds the submitted event, including invalid JSON when ingestion can retain it. JSON whitespace from a well-formed HTTP request is not guaranteed byte-for-byte; values and event contents are retained. Request-level authentication/identity/size failures are rejected before journaling.

## Delivery and crash behavior

The edge uses **at-least-once delivery**. A batch stays in SQLite until acknowledgements for all its submitted IDs are checked. Rejections move to a local rejected table. A lost acknowledgement causes a retry, which the server records as another receipt but not another analytical event.

The server appends, flushes and fsyncs its journal before the SQLite transaction commits; it returns acknowledgements afterward. SQLite uses WAL with FULL synchronous mode. On process restart, missing receipt IDs are re-materialized from complete journal lines. Only a torn final line is truncated. A corrupted complete record stops startup instead of silently throwing away evidence.

An exclusive directory lease prevents two collector processes or offline exporters from concurrently recovering/writing the same journal. Use one Uvicorn process; do not add `--workers`. While the collector runs, use the HTTP export button/API. For CLI export or copying the data directory, stop it cleanly first. Copy the entire directory rather than an active SQLite file. Preserve the bronze journal; it can rebuild materialized state after a process interruption. This is a local recovery design, not a remote backup or a guarantee against disk loss.

No automatic deletion/retention job runs. Archive completed experiments and monitor free disk space. A full disk returns storage errors; the edge retains unacknowledged events until its own configured capacity is exhausted.

## Fresh dashboard sessions

The dashboard resets at **collector startup**, including after an unexpected process stop. It first recovers the durable archive, then opens a new view containing only events and receipts added after that point. The boundary uses database row IDs, so it does not depend on robot or laptop clock accuracy. The snapshot includes a collection ID and start time for the browser.

Old robot readings, position trails, speed history, run choices, quality counters, and protocol metrics are excluded. The dashboard export button and export API also use this current session. Browser refreshes and launcher reuse do not reset the session. Shutdown leaves the last snapshot visibly stopped; the next collector start creates the empty view.

The archive is retained, including old event IDs for duplicate detection. An already accepted event retried after restart is counted as a duplicate receipt in the new session and does not restore old telemetry. An event delivered for the first time after startup belongs to the new session, even if the robot buffered it earlier. Old observations remain stale. Protocol messages split across sessions can have missing matching evidence; use an offline full-run export for complete experiment analysis.

To export the full archived live run across all dashboard sessions, stop the collector and run:

```powershell
python -m swarmlink_data export --data .runtime/live --run hotspot-live --output .runtime/exports/hotspot-live
```

Replace `hotspot-live` with the experiment's run ID. Use `--no-parquet` if the analytics extra is not installed. This command reads the archive without applying a dashboard session boundary. It does not restart the collector.

## Useful interfaces

| Endpoint | Behavior |
| --- | --- |
| `GET /api/health` | Process mode/version |
| UDP 8766 | Authenticated discovery by paired robots; disable with `serve --discovery-port 0` |
| `POST /api/events` | Authenticated single-event/batch ingestion |
| `GET /api/snapshot?run_id=...` | Current collection ID/start, robot state, history, quality and selected-run metrics |
| `WS /api/stream?run_id=...` | Snapshot every second; browser reconnects after a disconnect |
| `GET /api/compare` | Per-run metadata and protocol metrics |
| `GET /api/runs/{run_id}/export?format=csv` | Current session's committed run events as CSV; also supports `parquet` |
| `POST /api/demo/pause`, `/api/demo/interrupt` | Loopback demo only; never controls physical robots |

Pipeline counts/rate/event age cover **all runs in the current collector session**. Fleet history and sequence gaps cover the selected run within that session. The `recovered_receipts` API field is a separate startup recovery diagnostic for the archive. The browser labels a disconnected stream and fades the last snapshot; it does not keep pretending its last received snapshot is current.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Robot is unseen | Correct PC IP/port, process running, private LAN/firewall reachability, token and `X-Robot-ID`, matching run metadata |
| Robot online, measurements missing | Local drivers, ROS domain, topic names, sensor QoS compatibility; check topic ages |
| Robot stale after reconnect | Old outbox events are draining; wait for fresh observations, compare robot and PC clocks |
| 401/403 | Correct per-robot key; batch identity must match; simulation belongs in demo mode |
| Rejected records | Open quarantine reason; repair producer schema/run metadata. Do not modify an existing event ID's content. |
| Event ID collision | A producer reused an ID for another logical event; create a fresh event ID for corrected/new events |
| Sequence gaps | Late/out-of-order arrivals, refused samples at a full outbox, or genuine gaps. Counts only cover observed ranges. |
| Parquet unavailable | Install `.[analytics]` in the Python environment running the collector |
| Data directory already in use | Stop the other owner, or use the running server's export API |
| LiDAR values look sparse | Default agent samples to 72 rays; increase `--max-rays` if needed and measure bandwidth/storage |
| Delivery/detection blank | Instrument explicit protocol events. Sensors do not supply these metrics. |

## Current scope and scale

The minimum four-robot ingestion path and a 20-robot fixture are tested for correctness; one physical TurtleBot is connected. The CLI accepts 1–100 expected robots, but this is not a certified capacity. SQLite reads, JSONL recovery, dashboard histories, and whole-run exports will need profiling for long experiments and high-rate/full-scan collection. Large exports currently materialize a whole run in memory.

Before increasing scale, measure sample size, ingest rate, disk growth, query/export latency, and outage buffer duration. Then add a retention/archive policy and streaming exports; introduce a broker/time-series service only if those measurements justify it. The current code does not implement distributed collector HA, schema migrations, clock synchronization, arbitrary experiment parameters, cross-run confidence intervals, or consensus safety analysis.

The pinned package versions match the development environment used for this prototype. Review/update the dependency set and test it before broader deployment; there is no dependency-lock claim for transitive packages.
