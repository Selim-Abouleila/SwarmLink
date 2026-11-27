"""Import this helper inside the team's protocol process; it never controls a robot.

Each process uses its own outbox path and session, matching the ROS agent's
run/scenario/protocol metadata. Start a Publisher using this same outbox, robot
ID, collector URL, and the robot's SWARMLINK_ROBOT_TOKEN environment value.
"""
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from swarmlink_data.edge import Outbox


class ProtocolEvents:
    def __init__(self, outbox: Outbox, robot_id: str, run_id: str, protocol: str,
                 scenario: str = "hardware", simulation: bool = False, seed: int | None = None):
        self.outbox, self.robot_id, self.run_id = outbox, robot_id, run_id
        self.protocol, self.scenario, self.seed = protocol, scenario, seed
        self.source = "simulation" if simulation else "protocol"
        self.session = str(uuid4())
        self.sequence = 0
        self.lock = Lock()

    def emit(self, kind: str, payload: dict, sim_time_s: float | None = None):
        if self.source == "simulation" and sim_time_s is None:
            raise ValueError("Supply the simulator's event time")
        with self.lock:
            event = {"schema_version": 1, "event_id": str(uuid4()), "run_id": self.run_id,
                     "robot_id": self.robot_id, "session_id": self.session, "sequence": self.sequence,
                     "source": self.source, "kind": kind, "observed_at": datetime.now(timezone.utc).isoformat(),
                     "sim_time_s": sim_time_s, "scenario": self.scenario, "protocol": self.protocol,
                     "seed": self.seed, "payload": payload}
            self.sequence += 1
            self.outbox.append(event)
            return event["event_id"]


# Integration sketch (adapt inside the protocol's own send/receive handlers):
#
# outbox = Outbox(".runtime/protocol-robot-01.sqlite")
# trace = ProtocolEvents(outbox, "robot-01", "lab-baseline-001", "TEAM_PROTOCOL")
# worker = Publisher(outbox, "http://PC_IP:8765", "robot-01",
#                    os.environ["SWARMLINK_ROBOT_TOKEN"])
# worker.start()
# trace.emit("message_sent", {"message_id": "unique-message-uuid",
#     "recipients": ["robot-02", "robot-03", "robot-04"],
#     "message_type": "proposal", "size_bytes": 128, "attempt": 1})
#
# In robot-02's RECEIVING process (using its own trace and outbox):
# trace.emit("message_received", {"message_id": "unique-message-uuid",
#     "sender_id": "robot-01", "accepted": True})
#
# Emit an explicit detector result separately from the fault injector:
# trace.emit("fault_detected", {"fault_id": "episode-uuid", "target_id": "robot-03",
#     "fault_type": "transient", "detail": "Protocol detector fired"})
#
# Shut down with worker.stop(), then outbox.close(). Trace failures must be
# handled according to the team's experiment policy; do not silently discard
# observability errors or let this helper make protocol decisions.
