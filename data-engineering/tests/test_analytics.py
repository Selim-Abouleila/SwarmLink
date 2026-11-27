import pytest

from swarmlink_data.analytics import protocol_metrics


def item(kind, t, robot="robot-01", **payload):
    return {"run_id": "experiment", "robot_id": robot, "kind": kind, "metric_time": t, "payload": payload}


def test_delivery_is_per_recipient_first_delivery_not_retry_count():
    send = dict(message_id="m1", recipients=["robot-02", "robot-03"], size_bytes=100)
    received = dict(message_id="m1", sender_id="robot-01", accepted=True)
    events = [item("message_sent", 1, **send), item("message_received", 1.02, "robot-02", **received),
              item("message_sent", 2, **send), item("message_received", 2.04, "robot-02", **received),
              item("message_received", 1.03, "robot-03", **{**received, "accepted": False})]
    metrics = protocol_metrics(list(reversed(events)))
    assert metrics["delivery_pct"] == 50
    assert metrics["delivered"] == 1
    assert metrics["expected_deliveries"] == 2
    assert metrics["delivery_mean_ms"] == pytest.approx(20)
    assert metrics["transmissions"] == 2
    assert metrics["bytes_sent"] == 200


def test_fault_detection_requires_evidence_and_recovery_uses_fault_end():
    fault = dict(fault_id="f1", target_id="robot-03")
    events = [item("fault_injected", 5, **fault)]
    assert protocol_metrics(events)["undetected_faults"] == 1
    assert protocol_metrics(events)["detection_mean_ms"] is None
    events += [item("fault_detected", 5.25, **fault), item("fault_detected", 6, **fault),
               item("fault_ended", 10, **fault), item("recovered", 10.5, **fault)]
    metrics = protocol_metrics(events)
    assert metrics["detected_faults"] == 1
    assert metrics["detection_mean_ms"] == 250
    assert metrics["recovery_mean_ms"] == 500


def test_unknown_and_invalid_latency_are_not_zero_success():
    assert protocol_metrics([])["delivery_pct"] is None
    events = [item("message_sent", 10, message_id="m", recipients=["robot-02"], size_bytes=1),
              item("message_received", 9, "robot-02", message_id="m", sender_id="robot-01", accepted=True),
              item("message_received", 11, "robot-03", message_id="other", sender_id="robot-01", accepted=True)]
    metrics = protocol_metrics(events)
    assert metrics["delivered"] == 0
    assert metrics["delivery_mean_ms"] is None
    assert metrics["unmatched_receipts"] == 1
