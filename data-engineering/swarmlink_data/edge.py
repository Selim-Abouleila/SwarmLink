"""Standard-library-only outbox/HTTP transport, also usable from the simulator."""
from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

MAX_BATCH_BYTES = 768 * 1024  # headroom below the collector's 1 MiB limit


class Outbox:
    def __init__(self, path: Path | str, max_pending: int = 50000):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.max_pending = max_pending
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rejected (event_id TEXT PRIMARY KEY,body TEXT NOT NULL,reason TEXT NOT NULL);
        """)

    def size(self):
        with self.lock:
            return self.db.execute("SELECT count(*) FROM pending").fetchone()[0]

    def append(self, event: dict):
        body = json.dumps(event, allow_nan=False, separators=(",", ":"))
        if len(body.encode("utf-8")) > MAX_BATCH_BYTES - 16:
            raise ValueError("A single event exceeds the edge transport size limit")
        with self.lock, self.db:
            existing = self.db.execute("SELECT body FROM pending WHERE event_id=?", (event["event_id"],)).fetchone()
            if existing:
                if existing[0] != body:
                    raise ValueError("Cannot replace a queued event with different content")
                return
            if self.size() >= self.max_pending:
                raise BufferError("Outbox full: sample was not queued; collector connection needs attention")
            self.db.execute("INSERT INTO pending(event_id,body) VALUES (?,?)", (event["event_id"], body))

    def batch(self, limit=50, max_bytes=MAX_BATCH_BYTES):
        with self.lock:
            result, size = [], 13
            for row_id, body in self.db.execute("SELECT id,body FROM pending ORDER BY id LIMIT ?", (limit,)):
                added = len(body.encode("utf-8")) + 1
                if size + added > max_bytes:
                    break
                result.append((row_id, json.loads(body)))
                size += added
            return result

    def acknowledge(self, batch: list, results: list):
        # Never remove a batch on a truncated, mismatched or unrecognized acknowledgement.
        if len(batch) != len(results):
            raise ValueError("Acknowledgement count mismatch")
        for (_, event), result in zip(batch, results):
            if not isinstance(result, dict) or result.get("event_id") != event["event_id"] or result.get("status") not in ("accepted", "duplicate", "rejected"):
                raise ValueError("Invalid acknowledgement; pending events retained")
        with self.lock, self.db:
            for (row_id, event), result in zip(batch, results):
                if result["status"] == "rejected":
                    self.db.execute("INSERT OR REPLACE INTO rejected VALUES (?,?,?)", (event["event_id"], json.dumps(event), result.get("reason") or "Rejected by collector"))
                self.db.execute("DELETE FROM pending WHERE id=?", (row_id,))

    def close(self):
        with self.lock:
            self.db.close()


def send_batch(endpoint: str, robot_id: str, token: str, events: list[dict], timeout=5) -> list[dict]:
    data = json.dumps({"events": events}, allow_nan=False, separators=(",", ":")).encode()
    request = urllib.request.Request(endpoint.rstrip("/") + "/api/events", data=data, method="POST",
                                     headers={"Content-Type": "application/json", "X-Robot-ID": robot_id, "Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)["results"]


class Publisher(threading.Thread):
    def __init__(self, outbox: Outbox, endpoint: str | None, robot_id: str, token: str, log=print, resolver=None):
        super().__init__(name="swarmlink-outbox", daemon=True)
        self.outbox, self.endpoint, self.robot_id, self.token = outbox, endpoint, robot_id, token
        self.log = log
        self.resolver = resolver
        self.stopping = threading.Event()

    def run(self):
        backoff = 1
        while not self.stopping.is_set():
            batch = self.outbox.batch()
            if not batch:
                self.stopping.wait(.25)
                continue
            try:
                if not self.endpoint and self.resolver:
                    self.endpoint = self.resolver()
                    if self.endpoint:
                        self.log(f"Discovered collector at {self.endpoint}")
                if not self.endpoint:
                    raise OSError("No paired collector discovered yet")
                results = send_batch(self.endpoint, self.robot_id, self.token, [event for _, event in batch])
                self.outbox.acknowledge(batch, results)
                rejected = sum(r["status"] == "rejected" for r in results)
                if rejected:
                    self.log(f"{rejected} rejected events saved to the local rejected table")
                backoff = 1
            except (OSError, ValueError, KeyError, TypeError) as exc:
                if self.resolver:
                    self.endpoint = None  # Rediscover after DHCP changes or collector restart.
                self.log(f"Collector unavailable ({type(exc).__name__}); {self.outbox.size()} events retained, retry in {backoff}s")
                self.stopping.wait(backoff)
                backoff = min(30, backoff * 2)

    def stop(self):
        self.stopping.set()
        self.join(timeout=10)
