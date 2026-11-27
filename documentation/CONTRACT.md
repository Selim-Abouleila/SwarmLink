# Event contract v1

The executable contract is `swarmlink_data/contracts.py`. Unknown fields are rejected. SI units are used throughout. Optional unknown measurements are `null`, never a fabricated zero. Infinities and NaNs are invalid JSON measurements; the ROS agent converts invalid LiDAR returns to `null`.

## Common envelope

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `event_id` | UUID assigned once before the event enters the outbox; unchanged on every retry |
| `run_id` | Experiment identifier shared by all participating robots; letters/digits plus `_ . : -`, at most 100 characters |
| `robot_id` | Producer identity; `robot-01` to `robot-04` by default, letters/digits/`_`/`-`, at most 48 characters |
| `session_id` | New UUID whenever the producing process restarts |
| `sequence` | Nonnegative integer increasing within `(run_id, robot_id, session_id)` |
| `source` | `ros2`, `protocol`, or `simulation` |
| `kind` | One of the payload types below |
| `observed_at` | Timezone-aware observation time; producers send UTC ISO 8601 |
| `sim_time_s` | Required nonnegative simulation time for `source=simulation`; independent of wall time |
| `scenario`, `protocol`, `seed` | Run metadata; defaults `hardware`, `unreported`, and null seed |
| `payload` | Kind-specific object |

All events in a run must agree on scenario/protocol/seed and whether the run is simulated or physical. ROS and protocol producers can share a physical run. Set matching `--scenario` and `--protocol` on their producers. New experimental parameters require a new run ID; v1 does not yet model arbitrary configuration blobs.

Example physical telemetry:

```json
{
  "schema_version": 1,
  "event_id": "eeeb6546-8b83-4571-8959-3273687e365a",
  "run_id": "lab-20261127-baseline-01",
  "robot_id": "robot-01",
  "session_id": "4f139886-cdc6-4d41-9f04-4a7e6c07ef88",
  "sequence": 0,
  "source": "ros2",
  "kind": "telemetry",
  "observed_at": "2026-11-27T12:00:00Z",
  "scenario": "hardware",
  "protocol": "unreported",
  "payload": {
    "pose": {"x_m": 1.2, "y_m": 0.4, "yaw_rad": 0.1, "frame_id": "robot-01/odom", "map_id": null},
    "linear_m_s": 0.15,
    "angular_rad_s": 0.03,
    "battery_pct": 82.0,
    "battery_v": 11.8,
    "scan": {"angle_min_rad": -3.14, "angle_increment_rad": 1.57, "range_min_m": 0.12,
             "range_max_m": 8.0, "ranges_m": [1.3, null, 2.4, 1.8], "original_count": 360},
    "topic_age_s": {"odom": 0.05, "scan": 0.12, "battery": 0.3},
    "outbox_pending": 0
  }
}
```

The four-ray example is illustrative. The default agent retains up to 72 rays using a uniform stride and scales `angle_increment_rad` accordingly. `original_count` records the pre-sampling size; optional `stamp_s` preserves the ROS scan header timestamp. It does not preserve an entire unsampled raw scan. Use `--max-rays 360` for a 360-ray sensor when complete scans are needed, or up to 1440.

## Payloads

| Kind | Required payload |
| --- | --- |
| `telemetry` | Any subset of the example sensor fields. An empty payload is a heartbeat with unknown sensors. |
| `message_sent` | `message_id`, unique nonempty `recipients` excluding the sender, `message_type`, nonnegative `size_bytes`; `attempt` defaults to 1 |
| `message_received` | `message_id`, `sender_id`, boolean `accepted`; the envelope's `robot_id` is the recipient |
| `fault_injected` | `fault_id`, `target_id`, `fault_type` (`crash`, `transient`, `byzantine`), optional `detail` |
| `fault_detected` | Same fault fields; emitted by a detector, distinct from fault ground truth |
| `fault_ended` | Same fault fields; explicit end of the fault condition |
| `recovered` | Same fault fields; explicit restored service after fault end |
| `decision` | `decision_id`, string `value` |

`message_id` must remain unique for a sender within a run, including after a process restart. A retransmission retains the message ID and gets a **new event ID/sequence**, with an increased attempt. Transport retry of an already recorded event retains its **original event ID/sequence and content**. These are different operations.

Fault IDs identify a fault episode and must remain consistent across injection/detection/end/recovery events. A stale heartbeat is not automatically a `fault_detected` event. False-positive detector analysis and consensus agreement are future analytics, not inferred here.

## HTTP ingestion and acknowledgements

`POST /api/events` accepts one event object or `{"events": [event, ...]}` (1–100 items, request at most 1 MiB).

```text
Content-Type: application/json
X-Robot-ID: robot-01
Authorization: Bearer <robot-01-token>
```

The key authorizes only that producer's events, including every item in a batch. Live mode rejects simulation events. HTTP 401/403/413 rejects the request before journaling. HTTP 503 means persistence failed: keep the events and retry their same IDs. A successful request returns per-event results **after** journaling and materialization:

```json
{"results":[{"receipt_id":"...","event_id":"...","status":"accepted","reason":null}]}
```

- `accepted`: committed unique event. Remove it from the edge queue.
- `duplicate`: identical normalized content already committed for this event ID. Remove it from the queue.
- `rejected`: invalid schema, conflicting event ID/sequence, or conflicting run metadata. Retain it in the edge's `rejected` table for investigation; do not endlessly retry it.

Malformed JSON itself is journaled and quarantined, but cannot always echo an event ID. A well-formed producer must send valid finite JSON. The edge validates that every acknowledgement matches the pending event ID before removing any batch rows.

## Metric definitions

| Metric | Definition / scope |
| --- | --- |
| Delivery | Number of unique intended `(run, sender, message, recipient)` deliveries divided by expected deliveries. Only accepted receipts count. Multiple recipients contribute separate expected deliveries; retries do not inflate success. |
| Delivery latency | Earliest accepted receipt minus earliest send per intended recipient, in milliseconds. Negative clock differences are excluded. Mean/median are null without valid evidence. |
| Detection delay | First explicit detection minus injection per `(run, fault_id, target)`. Undetected faults count separately; their delay is null, not zero. |
| Recovery delay | First explicit recovery minus fault end for that episode. |
| Transmission bytes | Sum of `size_bytes` on send events, including retransmissions. It does not estimate link-layer overhead. |
| Sequence gaps | Sum of `max - min + 1 - count` in observed robot/session ranges for the selected run. Leading/trailing losses cannot be inferred. Late arrivals close gaps. |
| Online | Latest telemetry was observed and received no more than 6 seconds ago. This is agent telemetry freshness, not proof every sensor or robot function is healthy. |
| Topic freshness | Producer monotonic time since each topic callback; independent of PC clock synchronization. Missing/stale topics are omitted/null in the sample. |
| P95 event age | Collector wall-clock receipt time minus producer wall-clock observation time, over the last minute of arrivals. Negative differences are excluded, not represented as zero; the result is null without usable samples. Timestamps more than 100 ms ahead of receipt flag a clock mismatch. Includes queue delay; **not network RTT**. |
| Ingestion rate | Accepted events across all runs in the last 10 seconds divided by 10. |

Protocol metrics use `sim_time_s` for simulation events and `observed_at` for physical events. Collection wall time measures pipeline activity. Synchronize physical robot clocks before interpreting cross-robot latency. Compare like-for-like scenarios, seeds and protocols; this dashboard does not aggregate statistical confidence intervals across repeated runs.
