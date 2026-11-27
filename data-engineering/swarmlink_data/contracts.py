"""Versioned, source-independent event contract. All measurements use SI units."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RobotId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Pose(StrictModel):
    x_m: Finite
    y_m: Finite
    yaw_rad: Finite
    frame_id: Annotated[str, Field(min_length=1, max_length=100)]
    # Only set when the team has actually established a common map frame.
    map_id: Identifier | None = None


class Scan(StrictModel):
    angle_min_rad: Finite
    angle_increment_rad: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    range_min_m: Nonnegative
    range_max_m: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    ranges_m: Annotated[list[Nonnegative | None], Field(min_length=1, max_length=1440)]
    original_count: Annotated[int, Field(ge=1, le=100000, strict=True)]
    stamp_s: Nonnegative | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.range_max_m <= self.range_min_m:
            raise ValueError("range_max_m must exceed range_min_m")
        if self.original_count < len(self.ranges_m):
            raise ValueError("original_count cannot be smaller than retained ranges")
        if any(r is not None and not self.range_min_m <= r <= self.range_max_m for r in self.ranges_m):
            raise ValueError("invalid scan returns must be null, not out-of-range distances")
        return self


class Telemetry(StrictModel):
    pose: Pose | None = None
    linear_m_s: Finite | None = None
    angular_rad_s: Finite | None = None
    battery_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    battery_v: Nonnegative | None = None
    scan: Scan | None = None
    # Age is measured on the robot with a monotonic clock, independently per topic.
    topic_age_s: dict[Literal["odom", "scan", "battery"], Nonnegative | None] = Field(default_factory=dict)
    outbox_pending: Annotated[int, Field(ge=0, strict=True)] = 0


class MessageSent(StrictModel):
    message_id: Identifier
    recipients: Annotated[list[RobotId], Field(min_length=1, max_length=100)]
    message_type: Annotated[str, Field(min_length=1, max_length=60)]
    size_bytes: Annotated[int, Field(ge=0, le=10000000, strict=True)]
    attempt: Annotated[int, Field(ge=1, strict=True)] = 1

    @field_validator("recipients")
    @classmethod
    def unique_recipients(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("recipients must be unique")
        return value


class MessageReceived(StrictModel):
    message_id: Identifier
    sender_id: RobotId
    accepted: bool


class Fault(StrictModel):
    fault_id: Identifier
    target_id: RobotId
    fault_type: Literal["crash", "transient", "byzantine"]
    detail: Annotated[str, Field(max_length=300)] = ""


class Decision(StrictModel):
    decision_id: Identifier
    value: Annotated[str, Field(max_length=200)]


Kind = Literal["telemetry", "message_sent", "message_received", "fault_injected", "fault_detected", "fault_ended", "recovered", "decision"]
PAYLOADS = {
    "telemetry": Telemetry, "message_sent": MessageSent, "message_received": MessageReceived,
    "fault_injected": Fault, "fault_detected": Fault, "fault_ended": Fault, "recovered": Fault,
    "decision": Decision,
}


class Event(StrictModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    run_id: Identifier
    robot_id: RobotId
    session_id: UUID
    sequence: Annotated[int, Field(ge=0, strict=True)]
    source: Literal["simulation", "ros2", "protocol"]
    kind: Kind
    observed_at: datetime
    sim_time_s: Nonnegative | None = None
    scenario: Annotated[str, Field(min_length=1, max_length=100)] = "hardware"
    protocol: Annotated[str, Field(min_length=1, max_length=100)] = "unreported"
    seed: Annotated[int, Field(ge=0, strict=True)] | None = None
    payload: dict

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must contain a timezone (UTC recommended)")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_payload(self):
        if self.source == "simulation" and self.sim_time_s is None:
            raise ValueError("simulation events require sim_time_s")
        self.payload = PAYLOADS[self.kind].model_validate(self.payload).model_dump(mode="json")
        if self.kind == "message_sent" and self.robot_id in self.payload["recipients"]:
            raise ValueError("sender cannot be its own recipient")
        return self

    @property
    def metric_time(self) -> float:
        return self.sim_time_s if self.source == "simulation" else self.observed_at.timestamp()
