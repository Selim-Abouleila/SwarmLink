"""Exercise the real HTTP transport, including committed events with lost ACKs."""
import json
import socket
import threading
import time
import urllib.request

import uvicorn
from fastapi.responses import Response

from swarmlink_data.app import create_app
from swarmlink_data.edge import Outbox, Publisher


def test_four_http_publishers_recover_lost_acknowledgements(tmp_path, event):
    ids = [f"robot-{i:02d}" for i in range(1, 5)]
    keys = {robot: "transport-test-key-" + robot for robot in ids}
    app = create_app(tmp_path / "collector", keys=keys)
    lost = set()

    @app.middleware("http")
    async def lose_first_ack(request, call_next):
        response = await call_next(request)
        robot = request.headers.get("x-robot-id")
        if request.url.path == "/api/events" and response.status_code == 200 and robot not in lost:
            lost.add(robot)
            return Response("simulated lost acknowledgement after commit", status_code=503)
        return response

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    boxes, workers = [], []
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started, "HTTP test server did not start"
        for robot in ids:
            outbox = Outbox(tmp_path / f"{robot}.sqlite")
            outbox.append(event(robot_id=robot))
            boxes.append(outbox)
            worker = Publisher(outbox, None, robot, keys[robot], log=lambda message: None, resolver=lambda: endpoint)
            workers.append(worker)
            worker.start()
        while any(box.size() for box in boxes) and time.monotonic() < deadline:
            time.sleep(.05)
        assert all(box.size() == 0 for box in boxes)
        with urllib.request.urlopen(endpoint + "/api/snapshot", timeout=2) as response:
            snapshot = json.load(response)
        assert snapshot["pipeline"]["accepted"] == 4
        assert snapshot["pipeline"]["duplicate"] == 4
        assert snapshot["pipeline"]["rejected"] == 0
        assert all(robot["status"] == "online" for robot in snapshot["robots"])
    finally:
        for worker in workers:
            worker.stop()
        for box in boxes:
            box.close()
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
    assert not thread.is_alive()
