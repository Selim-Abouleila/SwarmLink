import copy

import pytest
from fastapi.testclient import TestClient

from swarmlink_data.app import MAX_BODY, create_app

TOKEN = "test-credential-robot-01-with-enough-entropy"
HEADERS = {"X-Robot-ID": "robot-01", "Authorization": "Bearer " + TOKEN}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, keys={"robot-01": TOKEN})) as client:
        yield client


def test_live_ingestion_requires_credentials(client, event):
    assert client.post("/api/events", json=event()).status_code == 401
    assert client.post("/api/events", headers={**HEADERS, "Authorization": TOKEN}, json=event()).status_code == 401
    response = client.post("/api/events", headers=HEADERS, json=event())
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "accepted"


def test_batch_cannot_impersonate_another_robot_or_mix_simulation(client, event):
    batch = [event(), event(robot_id="robot-02")]
    assert client.post("/api/events", headers=HEADERS, json={"events": batch}).status_code == 403
    assert client.get("/api/snapshot").json()["pipeline"]["accepted"] == 0
    assert client.post("/api/events", headers=HEADERS, json=event(source="simulation", sim_time_s=0)).status_code == 403


def test_invalid_payloads_and_transport_limits(client, event):
    rejected = client.post("/api/events", headers=HEADERS, json=event(payload={"battery_pct": -1}))
    assert rejected.json()["results"][0]["status"] == "rejected"
    assert client.post("/api/events", headers=HEADERS, content="{" * (MAX_BODY + 1)).status_code == 413
    assert client.post("/api/events", headers=HEADERS, json={"events": []}).status_code == 400
    malformed = client.post("/api/events", headers=HEADERS, content='{"not-json":')
    assert malformed.json()["results"][0]["status"] == "rejected"


def test_four_robot_snapshot_and_websocket(tmp_path, event):
    ids = [f"robot-{n:02d}" for n in range(1, 5)]
    with TestClient(create_app(tmp_path, keys={r: TOKEN for r in ids})) as client:
        for robot in ids:
            response = client.post("/api/events", headers={**HEADERS, "X-Robot-ID": robot}, json=event(robot_id=robot))
            assert response.json()["results"][0]["status"] == "accepted"
        with client.websocket_connect("/api/stream") as ws:
            snapshot = ws.receive_json()
            assert len(snapshot["robots"]) == 4
            assert all(r["status"] == "online" for r in snapshot["robots"])
            assert snapshot["mode"] == "live"
        assert client.get("/").status_code == 200
        for asset in ("app.mjs",):
            response = client.get(f"/static/{asset}")
            assert response.status_code == 200
            assert response.headers["content-type"].split(";")[0] in ("text/javascript", "application/javascript")
        assert client.post("/api/demo/interrupt").status_code == 404


def test_exports_and_unknown_run(client, event):
    client.post("/api/events", headers=HEADERS, json=event())
    csv = client.get("/api/runs/test-run/export?format=csv")
    assert csv.status_code == 200
    assert "battery_pct" in csv.text and "test-run" in csv.text
    assert client.get("/api/runs/unknown/export").status_code == 404
    assert client.get("/api/runs/test-run/export?format=xml").status_code == 400


def test_fail_closed_without_live_keys(tmp_path):
    with pytest.raises(ValueError, match="keys"):
        create_app(tmp_path)
