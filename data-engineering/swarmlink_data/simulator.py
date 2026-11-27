"""Deterministic telemetry fixtures; this is NOT the team's network/protocol simulator."""
from __future__ import annotations

import asyncio
import math
import random
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from .store import Store


class Demo:
    def __init__(self, store: Store, robots: int = 4, seed: int = 42):
        self.store, self.robots, self.seed = store, robots, seed
        self.random = random.Random(seed)
        self.run_id = "demo-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:4]
        self.sessions = {f"robot-{i:02d}": str(uuid4()) for i in range(1, robots + 1)}
        self.sequences = dict.fromkeys(self.sessions, 0)
        self.tick = 0
        self.paused = False
        self.fault_until = 0
        self.fault_id = None
        self.lock = threading.RLock()

    def event(self, robot: str, kind: str, payload: dict, sim_time: float | None = None) -> dict:
        sequence = self.sequences[robot]
        self.sequences[robot] += 1
        return {"schema_version": 1, "event_id": str(uuid4()), "run_id": self.run_id,
                "robot_id": robot, "session_id": self.sessions[robot], "sequence": sequence,
                "source": "simulation", "kind": kind, "observed_at": datetime.now(timezone.utc).isoformat(),
                "sim_time_s": self.tick * .5 if sim_time is None else sim_time,
                "scenario": "telemetry-fixture", "protocol": "fixture-only", "seed": self.seed, "payload": payload}

    def interrupt(self):
        with self.lock:
            self._interrupt()

    def _interrupt(self):
        if self.fault_until > time.monotonic():
            return
        self.fault_until = time.monotonic() + 12
        self.fault_id = "fault-" + uuid4().hex[:12]
        self.store.ingest(self.event("robot-01", "fault_injected", self._fault_payload()))

    def _fault_payload(self):
        return {"fault_id": self.fault_id, "target_id": f"robot-{self.robots:02d}", "fault_type": "transient",
                "detail": "Synthetic 12-second collector interruption; not a physical robot fault."}

    def step(self):
        with self.lock:
            self._step()

    def _step(self):
        if self.paused:
            return
        t = self.tick * .5
        batch = []
        if self.fault_id and time.monotonic() >= self.fault_until:
            target = f"robot-{self.robots:02d}"
            batch.extend([self.event(target, "fault_ended", self._fault_payload()), self.event(target, "recovered", self._fault_payload(), t + .06)])
            self.fault_id = None
        for i, robot in enumerate(self.sessions):
            if self.fault_id and robot == f"robot-{self.robots:02d}":
                self.sequences[robot] += 1  # Deliberately lost samples, visible as sequence gaps after recovery.
                continue
            phase = t * .075 + i * 1.1
            x, y = (i % 2) * 5 - 2.5 + 1.7 * math.cos(phase), (i // 2) * 4 - 2 + 1.25 * math.sin(phase)
            ranges = [round(1.9 + 1.3 * math.sin(j * .3 + phase) ** 2, 3) if j % 17 else None for j in range(72)]
            if i == 2:
                ranges[20] = round(.38 + .12 * math.sin(t * .1), 3)
            payload = {"pose": {"x_m": x, "y_m": y, "yaw_rad": phase + math.pi / 2, "frame_id": "map", "map_id": "demo-lab"},
                       "linear_m_s": .12 + .035 * math.sin(phase), "angular_rad_s": .075,
                       "battery_pct": max(8, 92 - i * 17 - t / 240), "battery_v": 11.3 - i * .2,
                       "scan": {"angle_min_rad": -math.pi, "angle_increment_rad": 2 * math.pi / 72,
                                "range_min_m": .12, "range_max_m": 8.0, "ranges_m": ranges, "original_count": 360},
                       "topic_age_s": {"odom": .05, "scan": .12, "battery": .3}, "outbox_pending": 0}
            batch.append(self.event(robot, "telemetry", payload))
        if self.tick % 4 == 0 and self.robots > 1:
            sender = "robot-01"
            receiver = f"robot-{2 + (self.tick // 4) % (self.robots - 1):02d}"
            mid = f"message-{self.tick}"
            batch.append(self.event(sender, "message_sent", {"message_id": mid, "recipients": [receiver], "message_type": "fixture-heartbeat", "size_bytes": 128}))
            if not (self.fault_id and receiver == f"robot-{self.robots:02d}"):
                batch.append(self.event(receiver, "message_received", {"message_id": mid, "sender_id": sender, "accepted": True}, t + .018 + self.random.random() * .035))
        if batch:
            for offset in range(0, len(batch), 100):
                self.store.ingest_batch(batch[offset:offset + 100])
        self.tick += 1

    async def run(self):
        while True:
            work = asyncio.create_task(asyncio.to_thread(self.step))
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                # Finish an in-progress journal commit before closing the store.
                await work
                raise
            await asyncio.sleep(.5)
