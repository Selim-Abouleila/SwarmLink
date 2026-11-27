#!/usr/bin/env python3
"""Read-only ROS 2 collector. Does not start drivers or publish movement commands."""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swarmlink_data.edge import Outbox, Publisher
from swarmlink_data.discovery import discover


def finite(value):
    return float(value) if value is not None and math.isfinite(value) else None


def battery_percent(value, scale="fraction"):
    """ROS specifies 0..1, but this TurtleBot driver publishes 0..100."""
    value = finite(value)
    maximum = 1 if scale == "fraction" else 100
    return value * (100 if scale == "fraction" else 1) if value is not None and 0 <= value <= maximum else None


def scan_payload(message, max_rays: int):
    """Retain a uniformly sampled scan, with the corresponding angular increment."""
    stride = max(1, math.ceil(len(message.ranges) / max_rays))
    ranges = [finite(v) if math.isfinite(v) and message.range_min <= v <= message.range_max else None for v in message.ranges[::stride]]
    if not ranges or message.angle_increment <= 0:
        return None
    return {"angle_min_rad": float(message.angle_min), "angle_increment_rad": float(message.angle_increment) * stride,
            "range_min_m": float(message.range_min), "range_max_m": float(message.range_max),
            "ranges_m": ranges, "original_count": len(message.ranges),
            "stamp_s": message.header.stamp.sec + message.header.stamp.nanosec / 1e9}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--run-id", required=True, help="Same experiment ID on all participating robots")
    parser.add_argument("--collector", help="Optional fixed http://PC_IP:8765; omit to discover the paired collector")
    parser.add_argument("--discovery-port", type=int, default=8766)
    parser.add_argument("--discovery-target", default="auto", help="auto checks the local subnet; alternatively use a known collector IP")
    parser.add_argument("--scenario", default="hardware")
    parser.add_argument("--protocol", default="unreported")
    parser.add_argument("--hz", type=float, default=2)
    parser.add_argument("--max-rays", type=int, default=72)
    parser.add_argument("--fresh-seconds", type=float, default=3)
    parser.add_argument("--odom-topic", default="/odom")
    parser.add_argument("--scan-topic", default="/scan")
    parser.add_argument("--battery-topic", default="/battery_state")
    parser.add_argument("--battery-scale", choices=("fraction", "percent"), default="fraction",
                        help="Use percent for TurtleBot drivers that publish 0..100 instead of the ROS-standard 0..1")
    parser.add_argument("--outbox", type=Path, default=Path.home() / ".local/state/swarmlink/outbox.sqlite")
    args = parser.parse_args()
    if not 0 < args.hz <= 20 or not 1 <= args.max_rays <= 1440 or args.fresh_seconds <= 0:
        parser.error("hz must be in (0,20], max-rays in [1,1440], and fresh-seconds positive")
    token = os.environ.get("SWARMLINK_ROBOT_TOKEN")
    if not token:
        parser.error("Set SWARMLINK_ROBOT_TOKEN; do not put credentials in command-line arguments")
    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from nav_msgs.msg import Odometry
        from sensor_msgs.msg import LaserScan, BatteryState
    except ImportError as exc:
        parser.error(f"Source the robot's ROS 2 environment first: {exc}")
    outbox = Outbox(args.outbox)

    class Collector(Node):
        def __init__(self):
            super().__init__("swarmlink_collector_" + args.robot_id.replace("-", "_"))
            self.latest = {}
            self.session, self.sequence = str(uuid4()), 0
            self.create_subscription(Odometry, args.odom_topic, lambda msg: self.observe("odom", msg), qos_profile_sensor_data)
            self.create_subscription(LaserScan, args.scan_topic, lambda msg: self.observe("scan", msg), qos_profile_sensor_data)
            self.create_subscription(BatteryState, args.battery_topic, lambda msg: self.observe("battery", msg), qos_profile_sensor_data)
            self.create_timer(1 / args.hz, self.sample)

        def observe(self, topic, message):
            self.latest[topic] = (time.monotonic(), message)

        def sample(self):
            now = time.monotonic()
            ages = {name: now - self.latest[name][0] if name in self.latest else None for name in ("odom", "scan", "battery")}
            fresh = {name: self.latest[name][1] for name, age in ages.items() if age is not None and age <= args.fresh_seconds}
            payload = {"topic_age_s": ages, "outbox_pending": outbox.size()}
            if "odom" in fresh:
                msg = fresh["odom"]
                q, p = msg.pose.pose.orientation, msg.pose.pose.position
                yaw = math.atan2(2 * (q.w*q.z + q.x*q.y), 1 - 2 * (q.y*q.y + q.z*q.z))
                if all(math.isfinite(v) for v in (p.x, p.y, yaw)):
                    payload["pose"] = {"x_m": p.x, "y_m": p.y, "yaw_rad": yaw, "frame_id": args.robot_id + "/" + (msg.header.frame_id or "odom"), "map_id": None}
                payload.update(linear_m_s=finite(msg.twist.twist.linear.x), angular_rad_s=finite(msg.twist.twist.angular.z))
            if "scan" in fresh:
                payload["scan"] = scan_payload(fresh["scan"], args.max_rays)
            if "battery" in fresh:
                msg = fresh["battery"]
                payload["battery_pct"] = battery_percent(msg.percentage, args.battery_scale)
                payload["battery_v"] = msg.voltage if math.isfinite(msg.voltage) and msg.voltage >= 0 else None
            event = {"schema_version": 1, "event_id": str(uuid4()), "run_id": args.run_id, "robot_id": args.robot_id,
                     "session_id": self.session, "sequence": self.sequence, "source": "ros2", "kind": "telemetry",
                     "observed_at": datetime.now(timezone.utc).isoformat(), "scenario": args.scenario,
                     "protocol": args.protocol, "payload": payload}
            self.sequence += 1
            try:
                outbox.append(event)
            except BufferError as exc:
                self.get_logger().error(str(exc))

    rclpy.init()
    node = Collector()
    resolver = None if args.collector else lambda: discover(args.robot_id, token, args.discovery_port, args.discovery_target)
    worker = Publisher(outbox, args.collector, args.robot_id, token, lambda msg: node.get_logger().warning(msg), resolver=resolver)
    worker.start()
    node.get_logger().info(f"Collecting {args.robot_id} at {args.hz} Hz, ROS_DOMAIN_ID={os.environ.get('ROS_DOMAIN_ID', '0')}; no robot control")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        node.destroy_node()
        rclpy.shutdown()
        outbox.close()


if __name__ == "__main__":
    main()
