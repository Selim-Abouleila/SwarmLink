from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .export import csv_text, parquet_bytes
from .discovery import DiscoveryResponder
from .simulator import Demo
from .store import Store, reject_constant

MAX_BODY = 1024 * 1024
STATIC = Path(__file__).with_name("static")


def create_app(data_dir: Path | str = ".runtime/live", demo: bool = False, robots: int = 4, keys: dict[str, str] | None = None,
               discovery_port: int | None = None, http_port: int = 8765, discovery_host: str = "0.0.0.0",
               on_shutdown: Callable[[], None] | None = None) -> FastAPI:
    keys = keys or {}
    if not demo and not keys:
        raise ValueError("Live ingestion requires per-robot keys; run the keys command first")

    @asynccontextmanager
    async def lifespan(app):
        app.state.shutdown_event = asyncio.Event()
        store = Store(data_dir, robots)
        app.state.store = store
        app.state.demo = Demo(store, robots) if demo else None
        task = asyncio.create_task(app.state.demo.run()) if demo else None
        responder = None
        try:
            if not demo and discovery_port:
                responder = DiscoveryResponder(keys, http_port, host=discovery_host, port=discovery_port)
                responder.start()
            yield
        finally:
            if responder:
                responder.stop()
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            store.close()

    app = FastAPI(title="SwarmLink data plane", version=__version__, lifespan=lifespan)
    app.state.shutdown_requested = False
    control_token = secrets.token_urlsafe(32)

    def require_local_control(request: Request):
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if not local or request.url.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise HTTPException(403, "Open the dashboard on the collector PC to shut it down")
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            raise HTTPException(403, "Cross-origin controls are disabled")

    def mode_state():
        return {"mode": "demo" if demo else "live", "demo_paused": app.state.demo.paused if demo else None,
                "shutting_down": app.state.shutdown_requested}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        return {"service": "swarmlink-data", "status": "stopping" if app.state.shutdown_requested else "ok",
                "mode": "demo" if demo else "live", "version": __version__, "schema_version": 1,
                "discovery_enabled": bool(discovery_port and not demo)}

    @app.get("/api/control")
    async def control(request: Request):
        require_local_control(request)
        return Response(json.dumps({"shutdown_available": on_shutdown is not None,
                                    "token": control_token if on_shutdown else None}),
                        media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.post("/api/shutdown", status_code=202)
    async def shutdown(request: Request):
        require_local_control(request)
        supplied = request.headers.get("x-swarmlink-control", "")
        if not hmac.compare_digest(supplied.encode(), control_token.encode()):
            raise HTTPException(403, "Invalid dashboard control token")
        if on_shutdown is None:
            raise HTTPException(409, "Start the collector with the SwarmLink launcher to enable shutdown")
        if not app.state.shutdown_requested:
            app.state.shutdown_requested = True
            app.state.shutdown_event.set()
            # Let the HTTP acknowledgement and shutdown snapshots reach open tabs.
            asyncio.get_running_loop().call_later(.25, on_shutdown)
        return {"status": "shutting_down"}

    @app.get("/api/snapshot")
    def snapshot(run_id: str | None = None):
        return {**app.state.store.snapshot(run_id), **mode_state()}

    @app.get("/api/compare")
    def compare():
        return [{**run, **app.state.store.metrics(run["run_id"])} for run in app.state.store.snapshot()["runs"]]

    @app.post("/api/events")
    async def ingest(request: Request):
        if app.state.shutdown_requested:
            raise HTTPException(503, "Collector is shutting down; keep events queued for retry")
        robot_id = request.headers.get("x-robot-id", "")
        if keys:
            expected = keys.get(robot_id)
            authorization = request.headers.get("authorization", "")
            actual = authorization.removeprefix("Bearer ")
            if not authorization.startswith("Bearer ") or not expected or not hmac.compare_digest(actual.encode("utf-8"), expected.encode("utf-8")):
                raise HTTPException(401, "Invalid robot credentials")
        elif request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "Anonymous demo ingestion is loopback-only")
        chunks, length = [], 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > MAX_BODY:
                raise HTTPException(413, "Maximum request size is 1 MiB")
            chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw, parse_constant=reject_constant)
            items = body.get("events") if isinstance(body, dict) and "events" in body else [body]
            if not isinstance(items, list) or not 1 <= len(items) <= 100:
                raise HTTPException(400, "Send one event or an events array of 1 to 100 events")
        except (ValueError, TypeError):
            items = [raw]  # Keep malformed JSON as a raw string in the receipt journal.
        if keys:
            for item in items:
                if isinstance(item, dict) and item.get("robot_id", robot_id) != robot_id:
                    raise HTTPException(403, "A robot key cannot publish another robot's events")
                if isinstance(item, dict) and item.get("source") == "simulation":
                    raise HTTPException(403, "Simulation events belong in a separate demo database")
        try:
            result = await asyncio.to_thread(app.state.store.ingest_batch, items)
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, "Storage unavailable; retry the same event IDs") from exc
        return {"results": result}

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str, format: str = "csv"):
        events = app.state.store.events(run_id)
        if not events:
            raise HTTPException(404, "Run not found")
        if format == "csv":
            data, media = csv_text(events), "text/csv"
        elif format == "parquet":
            try:
                data = parquet_bytes(events)
            except ImportError as exc:
                raise HTTPException(501, "Install this package with the analytics extra to export Parquet") from exc
            media = "application/vnd.apache.parquet"
        else:
            raise HTTPException(400, "format must be csv or parquet")
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="swarmlink-{run_id}.{format}"'})

    @app.post("/api/demo/{action}")
    def demo_action(action: str, request: Request):
        if not demo:
            raise HTTPException(404)
        if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "Demo controls are loopback-only")
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Cross-origin controls are disabled")
        if action == "pause":
            app.state.demo.paused = not app.state.demo.paused
        elif action == "interrupt":
            app.state.demo.interrupt()
        else:
            raise HTTPException(404)
        return {"paused": app.state.demo.paused}

    @app.websocket("/api/stream")
    async def stream(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        if origin and urlparse(origin).netloc != websocket.headers.get("host"):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        run_id = websocket.query_params.get("run_id")
        try:
            while True:
                data = await asyncio.to_thread(app.state.store.snapshot, run_id)
                await websocket.send_json({**data, **mode_state()})
                if app.state.shutdown_requested:
                    await websocket.close(code=1001, reason="Dashboard shut down")
                    return
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(app.state.shutdown_event.wait(), timeout=1)
        except (WebSocketDisconnect, RuntimeError, OSError):
            return

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
