"""Durable JSONL receipt journal, idempotent materialization, and fleet read models."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .analytics import nearest_obstacle, protocol_metrics
from .contracts import Event
from .lease import DirectoryLease


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def reject_constant(value):
    raise ValueError(f"{value} is not a JSON number; use null for unavailable measurements")


class Store:
    def __init__(self, directory: Path | str, expected_robots: int = 4):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lease = DirectoryLease(self.directory)
        try:
            self._initialize(expected_robots)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self._lease.close()
            raise

    def _initialize(self, expected_robots):
        self.raw_dir = self.directory / "bronze"
        self.raw_dir.mkdir(exist_ok=True)
        self.expected_ids = [f"robot-{n:02d}" for n in range(1, expected_robots + 1)]
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "fleet.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS receipts (
                receipt_id TEXT PRIMARY KEY, received_at REAL NOT NULL, run_id TEXT,
                status TEXT NOT NULL, reason TEXT, event_id TEXT, raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
                run_id TEXT NOT NULL, robot_id TEXT NOT NULL, session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL,
                observed_at REAL NOT NULL, received_at REAL NOT NULL, metric_time REAL NOT NULL,
                event_json TEXT NOT NULL,
                UNIQUE(run_id, robot_id, session_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS events_run_kind_time ON events(run_id,kind,observed_at);
            CREATE INDEX IF NOT EXISTS events_robot ON events(run_id,robot_id,kind,observed_at);
            CREATE INDEX IF NOT EXISTS events_received ON events(received_at);
            CREATE INDEX IF NOT EXISTS receipts_time ON receipts(received_at);
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, source_group TEXT NOT NULL, scenario TEXT NOT NULL,
                protocol TEXT NOT NULL, seed INTEGER, created_at REAL NOT NULL
            );
        """)
        self.recovered_receipts = 0
        self._metric_cache: dict[str, dict] = {}
        self._recover()

    def _recover(self):
        # Journal fsync precedes SQLite commit. Reapply only missing receipt IDs.
        for path in sorted(self.raw_dir.glob("date=*/receipts.jsonl")):
            good_offset = 0
            with path.open("rb") as stream:
                while line := stream.readline():
                    if not line.endswith(b"\n"):
                        break  # Only a torn final write may be discarded.
                    try:
                        receipt = json.loads(line)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise RuntimeError(f"Corrupted complete journal record in {path}") from exc
                    if not self.db.execute("SELECT 1 FROM receipts WHERE receipt_id=?", (receipt["receipt_id"],)).fetchone():
                        with self.db:
                            self._materialize(receipt)
                        self.recovered_receipts += 1
                    good_offset = stream.tell()
            if path.stat().st_size != good_offset:
                with path.open("r+b") as stream:
                    stream.truncate(good_offset)

    def close(self):
        with self.lock:
            self.db.close()
            self._lease.close()

    def ingest(self, raw: dict | str) -> dict:
        return self.ingest_batch([raw])[0]

    def ingest_batch(self, items: list[dict | str]) -> list[dict]:
        if not 1 <= len(items) <= 100:
            raise ValueError("batch must contain 1 to 100 events")
        received = time.time()
        receipts = [{"receipt_id": str(uuid4()), "received_at": received,
                     "raw_json": item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)} for item in items]
        day = datetime.fromtimestamp(received, timezone.utc).strftime("%Y-%m-%d")
        with self.lock:
            path = self.raw_dir / f"date={day}" / "receipts.jsonl"
            path.parent.mkdir(exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.writelines(canonical(receipt) + "\n" for receipt in receipts)
                stream.flush()
                os.fsync(stream.fileno())
            with self.db:
                return [self._materialize(receipt) for receipt in receipts]

    def _materialize(self, receipt: dict) -> dict:
        status, reason, event_id, run_id = "rejected", None, None, None
        try:
            raw = json.loads(receipt["raw_json"], parse_constant=reject_constant)
            if isinstance(raw, dict) and isinstance(raw.get("run_id"), str):
                run_id = raw["run_id"][:100]
            if isinstance(raw, dict) and isinstance(raw.get("event_id"), str):
                # Echo rejected IDs so the edge can quarantine instead of retrying forever.
                event_id = raw["event_id"][:100]
            event = Event.model_validate(raw)
            document = event.model_dump(mode="json")
            encoded = canonical(document)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            event_id, run_id = str(event.event_id), event.run_id
            old = self.db.execute("SELECT content_hash FROM events WHERE event_id=?", (event_id,)).fetchone()
            collision = self.db.execute("SELECT event_id FROM events WHERE run_id=? AND robot_id=? AND session_id=? AND sequence=?",
                                        (run_id, event.robot_id, str(event.session_id), event.sequence)).fetchone()
            group = "simulation" if event.source == "simulation" else "physical"
            run = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if old:
                if old["content_hash"] != digest:
                    raise ValueError("event_id was reused with different content")
                status = "duplicate"
            elif collision:
                raise ValueError("sequence was reused within this robot/session/run")
            elif run and (run["source_group"], run["scenario"], run["protocol"], run["seed"]) != (group, event.scenario, event.protocol, event.seed):
                raise ValueError("run metadata conflicts; use a new run_id for a new experiment")
            else:
                self.db.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?,?)",
                                (run_id, group, event.scenario, event.protocol, event.seed, receipt["received_at"]))
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (event_id, digest, run_id, event.robot_id, str(event.session_id), event.sequence,
                                 event.source, event.kind, event.observed_at.timestamp(), receipt["received_at"], event.metric_time, encoded))
                if event.kind != "telemetry":
                    self._metric_cache.pop(run_id, None)
                status = "accepted"
        except ValidationError as exc:
            reason = "; ".join(".".join(str(p) for p in e["loc"]) + ": " + e["msg"] for e in exc.errors(include_input=False))[:1200]
        except (ValueError, TypeError) as exc:
            reason = str(exc)[:1200]
        self.db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?)",
                        (receipt["receipt_id"], receipt["received_at"], run_id, status, reason, event_id, receipt["raw_json"]))
        return {"receipt_id": receipt["receipt_id"], "event_id": event_id, "status": status, "reason": reason}

    def events(self, run_id: str) -> list[dict]:
        with self.lock:
            return [self._event_row(row) for row in self.db.execute("SELECT * FROM events WHERE run_id=? ORDER BY metric_time,event_id", (run_id,))]

    @staticmethod
    def _event_row(row) -> dict:
        event = json.loads(row["event_json"])
        event.update(received_at=row["received_at"], metric_time=row["metric_time"])
        return event

    def metrics(self, run_id: str) -> dict:
        with self.lock:
            if run_id not in self._metric_cache:
                rows = self.db.execute("SELECT * FROM events WHERE run_id=? AND kind!='telemetry'", (run_id,))
                self._metric_cache[run_id] = protocol_metrics([self._event_row(r) for r in rows])
            return dict(self._metric_cache[run_id])

    def snapshot(self, run_id: str | None = None, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.lock:
            runs = [dict(r) for r in self.db.execute("SELECT * FROM runs ORDER BY created_at DESC")]
            selected = run_id or (runs[0]["run_id"] if runs else None)
            counts = {r["status"]: r["n"] for r in self.db.execute("SELECT status,count(*) AS n FROM receipts GROUP BY status")}
            latest = self.db.execute("""SELECT * FROM (
                SELECT *,row_number() OVER (PARTITION BY robot_id ORDER BY observed_at DESC,received_at DESC) AS rn
                FROM events WHERE run_id=? AND kind='telemetry') WHERE rn=1""", (selected,)).fetchall()
            ids = list(dict.fromkeys(self.expected_ids + [r["robot_id"] for r in latest]))
            latest_by_id = {r["robot_id"]: r for r in latest}
            robots = []
            for robot_id in ids:
                row = latest_by_id.get(robot_id)
                item = {"robot_id": robot_id, "status": "unseen", "payload": {}, "age_s": None, "receipt_age_s": None, "source": None, "history": []}
                if row:
                    event = json.loads(row["event_json"])
                    age, receipt_age = now - row["observed_at"], max(0, now - row["received_at"])
                    item.update(payload=event["payload"], source=event["source"], age_s=max(0, age), receipt_age_s=receipt_age,
                                observed_at=event["observed_at"], session_id=event["session_id"], sequence=event["sequence"],
                                status="stale" if receipt_age > 6 or age > 6 else "online", clock_skew=row["received_at"] - row["observed_at"] < -.1,
                                nearest_obstacle_m=nearest_obstacle(event["payload"]))
                    history = self.db.execute("""SELECT observed_at,event_json FROM events WHERE run_id=? AND robot_id=? AND kind='telemetry'
                        ORDER BY observed_at DESC LIMIT 120""", (selected, robot_id)).fetchall()
                    item["history"] = [{"t": h["observed_at"], "pose": (p := json.loads(h["event_json"])["payload"]).get("pose"),
                                        "battery_pct": p.get("battery_pct"), "speed": p.get("linear_m_s")} for h in reversed(history)]
                robots.append(item)
            gaps = self.db.execute("""SELECT coalesce(sum(hi-lo+1-n),0) FROM (
                SELECT max(sequence) hi,min(sequence) lo,count(*) n FROM events WHERE run_id=? GROUP BY robot_id,session_id)""", (selected,)).fetchone()[0]
            recent_count = self.db.execute("SELECT count(*) FROM events WHERE received_at>?", (now - 10,)).fetchone()[0]
            raw_ages = [r[0] * 1000 for r in self.db.execute("SELECT received_at-observed_at FROM events WHERE received_at>?", (now - 60,))]
            ages = [value for value in raw_ages if value >= 0]
            ages.sort()
            recent = [self._event_row(r) for r in self.db.execute("SELECT * FROM events WHERE run_id=? ORDER BY received_at DESC,rowid DESC LIMIT 16", (selected,))]
            quarantine = [dict(r) for r in self.db.execute("SELECT received_at,event_id,reason FROM receipts WHERE status='rejected' ORDER BY received_at DESC LIMIT 12")]
            return {
                "server_time": now, "run_id": selected, "runs": runs, "robots": robots,
                "pipeline": {"accepted": counts.get("accepted", 0), "duplicate": counts.get("duplicate", 0), "rejected": counts.get("rejected", 0),
                             "events_per_s": recent_count / 10, "p95_event_age_ms": ages[min(len(ages)-1, int(len(ages)*.95))] if ages else None,
                             "future_timestamps": sum(age < -100 for age in raw_ages),
                             "sequence_gaps": gaps, "recovered_receipts": self.recovered_receipts},
                "protocol": self.metrics(selected) if selected else protocol_metrics([]),
                "recent_events": recent, "quarantine": quarantine,
            }
