from datetime import datetime, timezone
from uuid import uuid4

import pytest

from swarmlink_data.store import Store


@pytest.fixture
def event():
    def make(**overrides):
        data = {"schema_version": 1, "event_id": str(uuid4()), "run_id": "test-run",
                "robot_id": "robot-01", "session_id": str(uuid4()), "sequence": 0,
                "source": "ros2", "kind": "telemetry", "observed_at": datetime.now(timezone.utc).isoformat(),
                "payload": {"battery_pct": 80, "linear_m_s": .12,
                            "pose": {"x_m": 1, "y_m": 2, "yaw_rad": 0, "frame_id": "robot-01/odom"}}}
        data.update(overrides)
        return data
    return make


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "data")
    yield instance
    instance.close()
