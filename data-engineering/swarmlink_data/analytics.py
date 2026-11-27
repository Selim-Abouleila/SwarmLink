"""The same pure functions serve the dashboard, tests, and exported KPIs."""
from __future__ import annotations

import statistics


def protocol_metrics(events: list[dict]) -> dict:
    """First delivery per (run, sender, message, recipient); retries do not inflate delivery."""
    sent, received, faults, detections, endings, recoveries = {}, {}, {}, {}, {}, {}
    transmissions = total_bytes = 0
    for event in events:
        p, t = event["payload"], event["metric_time"]
        run, robot, kind = event["run_id"], event["robot_id"], event["kind"]
        if kind == "message_sent":
            transmissions += 1
            total_bytes += p["size_bytes"]
            for recipient in p["recipients"]:
                key = (run, robot, p["message_id"], recipient)
                sent[key] = min(t, sent.get(key, t))
        elif kind == "message_received" and p["accepted"]:
            key = (run, p["sender_id"], p["message_id"], robot)
            received[key] = min(t, received.get(key, t))
        elif kind == "fault_injected":
            key = (run, p["fault_id"], p["target_id"])
            faults[key] = min(t, faults.get(key, t))
        elif kind == "fault_detected":
            key = (run, p["fault_id"], p["target_id"])
            detections[key] = min(t, detections.get(key, t))
        elif kind == "fault_ended":
            key = (run, p["fault_id"], p["target_id"])
            endings[key] = min(t, endings.get(key, t))
        elif kind == "recovered":
            key = (run, p["fault_id"], p["target_id"])
            recoveries[key] = min(t, recoveries.get(key, t))
    delivered = [k for k in sent if k in received and received[k] >= sent[k]]
    latencies = [(received[k] - sent[k]) * 1000 for k in delivered]
    detected = [k for k in faults if k in detections and detections[k] >= faults[k]]
    delays = [(detections[k] - faults[k]) * 1000 for k in detected]
    recovery = [(recoveries[k] - endings[k]) * 1000 for k in endings if k in recoveries and recoveries[k] >= endings[k]]
    return {
        "expected_deliveries": len(sent), "delivered": len(delivered),
        "delivery_pct": 100 * len(delivered) / len(sent) if sent else None,
        "delivery_mean_ms": statistics.mean(latencies) if latencies else None,
        "delivery_median_ms": statistics.median(latencies) if latencies else None,
        "transmissions": transmissions, "bytes_sent": total_bytes,
        "unmatched_receipts": len(set(received) - set(sent)),
        "injected_faults": len(faults), "detected_faults": len(detected),
        "undetected_faults": len(faults) - len(detected),
        "detection_mean_ms": statistics.mean(delays) if delays else None,
        "recovery_mean_ms": statistics.mean(recovery) if recovery else None,
    }


def nearest_obstacle(payload: dict) -> float | None:
    scan = payload.get("scan")
    if not scan:
        return None
    valid = [r for r in scan["ranges_m"] if r is not None]
    return min(valid) if valid else None
