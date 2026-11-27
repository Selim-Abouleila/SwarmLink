import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from swarmlink_data.app import create_app
from swarmlink_data.store import Store

TOKEN = "test-robot-token-for-dashboard-control"


def browser_client(app, peer="127.0.0.1", hostname="localhost:8765"):
    async def scoped(scope, receive, send):
        await app({**scope, "client": (peer, 42000)}, receive, send)
    return TestClient(scoped, base_url=f"http://{hostname}")


def test_shutdown_notifies_tabs_preserves_data_and_is_idempotent(tmp_path, event):
    stopped = threading.Event()
    calls = []

    def stop():
        calls.append("stop")
        stopped.set()

    app = create_app(tmp_path, keys={"robot-01": TOKEN}, on_shutdown=stop)
    with browser_client(app) as client:
        headers = {"X-Robot-ID": "robot-01", "Authorization": "Bearer " + TOKEN}
        assert client.post("/api/events", headers=headers, json=event()).status_code == 200
        control = client.get("/api/control")
        assert control.headers["cache-control"] == "no-store"
        assert control.json()["shutdown_available"] is True
        admin = {"X-SwarmLink-Control": control.json()["token"], "Origin": "http://localhost:8765"}
        with client.websocket_connect("/api/stream") as ws:
            assert ws.receive_json()["shutting_down"] is False
            assert client.post("/api/shutdown", headers=admin).status_code == 202
            assert client.post("/api/shutdown", headers=admin).status_code == 202
            assert ws.receive_json()["shutting_down"] is True
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        assert stopped.wait(2)
        assert calls == ["stop"]
        assert client.get("/api/health").json()["status"] == "stopping"
        assert client.post("/api/events", headers=headers, json=event(sequence=2)).status_code == 503
    reopened = Store(tmp_path)
    try:
        assert reopened.snapshot()["pipeline"]["accepted"] == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("peer,hostname", [("172.20.10.2", "localhost:8765"),
                                        ("127.0.0.1", "evil.example:8765"),
                                        ("127.0.0.1", "172.20.10.5:8765")])
def test_control_rejects_remote_clients_and_rebound_hosts(tmp_path, peer, hostname):
    app = create_app(tmp_path, keys={"robot-01": TOKEN}, on_shutdown=lambda: None)
    with browser_client(app, peer, hostname) as client:
        assert client.get("/api/control").status_code == 403
        assert client.post("/api/shutdown", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
        assert not app.state.shutdown_requested


def test_control_rejects_cross_origin_and_missing_or_invalid_token(tmp_path):
    app = create_app(tmp_path, keys={"robot-01": TOKEN}, on_shutdown=lambda: None)
    with browser_client(app) as client:
        token = client.get("/api/control").json()["token"]
        assert client.get("/api/control", headers={"Origin": "https://evil.example"}).status_code == 403
        for headers in ({}, {"X-SwarmLink-Control": "bad"},
                        {"X-SwarmLink-Control": token, "Origin": "https://evil.example"}):
            assert client.post("/api/shutdown", headers=headers).status_code == 403
        assert not app.state.shutdown_requested


def test_control_disabled_without_server_lifecycle_callback(tmp_path):
    with browser_client(create_app(tmp_path, keys={"robot-01": TOKEN})) as client:
        assert client.get("/api/control").json() == {"shutdown_available": False, "token": None}
