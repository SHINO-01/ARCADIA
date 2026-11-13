"""Telemetry throttling node.

Subscribes to every robot's high-rate /odom (50 Hz), /imu and /battery streams, fuses them into
one compact arcadia_msgs/Telemetry sample per robot and republishes at a sustainable rate
(default 5 Hz) on /arcadia/telemetry - the only telemetry topic the backend bridge consumes.
This is the bandwidth budget for a rural IoT uplink: ~5 frames/s/robot instead of ~110 msgs/s.
"""
from __future__ import annotations

import math
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from arcadia_msgs.msg import Battery, Event, Telemetry


class TelemetryThrottle(Node):
    def __init__(self) -> None:
        super().__init__("telemetry_throttle")
        self.declare_parameter("rate_hz", 5.0)
        self.declare_parameter("robots", ["uav-1", "ugv-1", "agv-1"])
        self.rate = float(self.get_parameter("rate_hz").value)
        robots = list(self.get_parameter("robots").value)
        self.latest: dict[str, dict] = {}
        self.status: dict[str, str] = {}
        self.mission: dict[str, str] = {}
        self.types: dict[str, str] = {r: "scout_uav" if r.startswith("uav") else "logistics_agv" if r.startswith("agv") else "seeder_ugv" for r in robots}
        qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT)
        for r in robots:
            ns = f"/arcadia/{r.replace('-', '_')}"  # ROS topic names cannot contain hyphens
            self.create_subscription(Odometry, f"{ns}/odom", lambda m, r=r: self.on_odom(r, m), qos)
            self.create_subscription(Battery, f"{ns}/battery", lambda m, r=r: self.on_batt(r, m), 10)
        self.create_subscription(Event, "/arcadia/event", self.on_event, 50)
        self.pub = self.create_publisher(Telemetry, "/arcadia/telemetry", 20)
        self.create_timer(1.0 / self.rate, self.flush)
        self.in_count = 0
        self.out_count = 0
        self.create_timer(10.0, self.report)
        self.get_logger().info(f"throttling {len(robots)} robots to {self.rate:.1f} Hz")

    def on_odom(self, r: str, m: Odometry) -> None:
        self.in_count += 1
        q = m.pose.pose.orientation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        d = self.latest.setdefault(r, {})
        d.update(x=m.pose.pose.position.x, y=m.pose.pose.position.y, z=m.pose.pose.position.z, heading=yaw,
                 speed=m.twist.twist.linear.x, t=time.time(), dirty=True)

    def on_batt(self, r: str, m: Battery) -> None:
        self.in_count += 1
        d = self.latest.setdefault(r, {})
        d.update(battery_pct=m.pct, battery_v=m.voltage, power_w=m.power_w, charging=m.charging)

    def on_event(self, e: Event) -> None:
        # Track status/mission transitions so the compact frame carries them
        if not e.robot_id:
            return
        k = e.kind
        if k == "mission_started":
            self.mission[e.robot_id] = e.mission_id
            self.status[e.robot_id] = "towing" if '"tow"' in e.payload_json else "returning" if '"charge"' in e.payload_json else "active"
        elif k in ("mission_complete", "mission_failed"):
            self.mission[e.robot_id] = ""
            if self.status.get(e.robot_id) in ("active", "returning", "towing"):
                self.status[e.robot_id] = "idle"
        elif k == "fault_detected":
            self.status[e.robot_id] = "stranded" if ('"motor_jam"' in e.payload_json or '"battery_drop"' in e.payload_json) else "fault"
        elif k == "fault_cleared":
            self.status[e.robot_id] = "charging" if '"recharged": true' in e.payload_json or '"recharged":true' in e.payload_json else "idle"
        elif k == "charging_started":
            self.status[e.robot_id] = "charging"
        elif k == "charged":
            self.status[e.robot_id] = "idle"
        elif k == "robot_power":
            self.status[e.robot_id] = "idle" if '"active": true' in e.payload_json or '"active":true' in e.payload_json else "offline"
        elif k == "tow_attached":
            try:
                import json
                tgt = json.loads(e.payload_json).get("target")
                if tgt:
                    self.status[tgt] = "towed"
            except Exception:  # noqa: BLE001
                pass

    def flush(self) -> None:
        now = self.get_clock().now().to_msg()
        for r, d in self.latest.items():
            if not d.get("dirty") or "x" not in d:
                continue
            d["dirty"] = False
            m = Telemetry()
            m.stamp = now
            m.robot_id = r
            m.type = self.types.get(r, "")
            m.status = self.status.get(r, "idle")
            m.x, m.y, m.z, m.heading, m.speed = d["x"], d["y"], d["z"], d["heading"], d["speed"]
            m.battery_pct = float(d.get("battery_pct", 100.0))
            m.battery_v = float(d.get("battery_v", 48.0))
            m.motor_temp_c = float(d.get("motor_temp_c", 35.0))
            m.cpu_load = 0.2 + (0.3 if self.mission.get(r) else 0.0)
            m.mission_id = self.mission.get(r, "")
            self.pub.publish(m)
            self.out_count += 1

    def report(self) -> None:
        self.get_logger().debug(f"telemetry in={self.in_count} out={self.out_count} ({100*self.out_count/max(1,self.in_count):.0f}%)")
        self.in_count = self.out_count = 0


def main(args=None) -> None:
    rclpy.init(args=args)
    node = TelemetryThrottle()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
