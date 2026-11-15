"""Webots *extern* robot controller that is also a rclpy node.

One process per robot (launched by arcadia_bringup with WEBOTS_CONTROLLER_URL set). It:
  * drives the Webots robot (differential drive UGV/AGV, or kinematic UAV) towards mission
    waypoints with pure-pursuit steering and LiDAR-based obstacle avoidance,
  * runs the torque-coupled battery model (motor torque feedback -> watts),
  * publishes nav_msgs/Odometry (50 Hz), sensor_msgs/Imu, arcadia_msgs/Battery, diagnostics,
  * publishes domain events (arcadia_msgs/Event) and listens to arcadia_msgs/Command,
  * honours Chaos Engine faults (motor jam = wheel locked, battery drop, latency = delayed odom,
    sensor glitch = biased localisation).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from collections import deque

from arcadia_sim.webots_env import setup_controller_path

setup_controller_path()
from controller import Supervisor  # noqa: E402  (Webots ctypes API; supervisor TRUE lets the UAV move itself)

import rclpy  # noqa: E402
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue  # noqa: E402
from geometry_msgs.msg import Quaternion, Twist  # noqa: E402
from nav_msgs.msg import Odometry  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import QoSProfile, ReliabilityPolicy  # noqa: E402
from sensor_msgs.msg import Imu  # noqa: E402

from arcadia_msgs.msg import Battery, Command, Event, State  # noqa: E402
from arcadia_sim.behaviour import BatteryModel, Behaviour  # noqa: E402

SPECS = {
    "scout_uav": {"max_speed": 6.0, "battery_wh": 900, "idle_w": 25, "solar_skin_w": 12, "cruise_alt": 6.0, "sensor_radius_m": 9.0, "v": 22.2},
    "seeder_ugv": {"max_speed": 1.4, "battery_wh": 2400, "idle_w": 60, "solar_skin_w": 0, "cruise_alt": 0.0, "sensor_radius_m": 2.0, "v": 48.0},
    "cropcare_ugv": {"max_speed": 1.8, "battery_wh": 1200, "idle_w": 50, "solar_skin_w": 40, "cruise_alt": 0.0, "sensor_radius_m": 1.5, "v": 48.0},
    "harvester_ugv": {"max_speed": 1.0, "battery_wh": 3200, "idle_w": 70, "solar_skin_w": 0, "cruise_alt": 0.0, "sensor_radius_m": 1.2, "v": 48.0},
    "logistics_agv": {"max_speed": 1.6, "battery_wh": 6000, "idle_w": 80, "solar_skin_w": 0, "cruise_alt": 0.0, "sensor_radius_m": 2.5, "v": 72.0},
}
WHEEL_RADIUS = 0.11
AXLE = 0.52


def yaw_to_quat(yaw: float) -> Quaternion:
    return Quaternion(x=0.0, y=0.0, z=math.sin(yaw / 2), w=math.cos(yaw / 2))


class ArcadiaRobot(Node):
    def __init__(self) -> None:
        self.wb = Supervisor()
        name = self.wb.getName()  # e.g. uav-1
        self.self_node = self.wb.getSelf()
        self.tr_field = self.self_node.getField('translation')
        self.rot_field = self.self_node.getField('rotation')
        super().__init__(f"robot_{name.replace('-', '_')}")
        self.robot_id = name
        self.robot_type = self.wb.getCustomData() or ("scout_uav" if name.startswith("uav") else "logistics_agv" if name.startswith("agv") else "seeder_ugv")
        self.spec = SPECS.get(self.robot_type, SPECS["seeder_ugv"])
        self.dt = self.wb.getBasicTimeStep() / 1000.0
        self.is_uav = self.robot_type == "scout_uav"
        self.active = not name.startswith("agv")  # the AGV boots powered-down (hardware cap)
        self.home_xy: tuple[float, float] | None = None
        self.docks: list[tuple[float, float]] = []
        self.irradiance = 1.0

        self.b = Behaviour(name, self.robot_type, self.spec,
                           BatteryModel(self.spec["battery_wh"], idle_w=self.spec["idle_w"], solar_skin_w=self.spec["solar_skin_w"],
                                        nominal_v=self.spec["v"]))

        # ---- devices
        self.gps = self.wb.getDevice("gps"); self.gps.enable(int(self.dt * 1000))
        self.imu = self.wb.getDevice("imu"); self.imu.enable(int(self.dt * 1000))
        # Gyro/accelerometer need a Physics node; the kinematic UAV has none, so they may be absent.
        self.gyro = self.wb.getDevice("gyro")
        self.accel = self.wb.getDevice("accel")
        for dev in (self.gyro, self.accel):
            if dev is not None:
                dev.enable(int(self.dt * 1000))
        self.lidar = None
        self.motors = []
        if self.is_uav:
            self.props = [self.wb.getDevice(f"prop{i}") for i in range(1, 5)]
            for p in self.props:
                p.setPosition(float("inf")); p.setVelocity(0.0)
        else:
            self.motors = [self.wb.getDevice(n) for n in ("fl_wheel", "fr_wheel", "rl_wheel", "rr_wheel")]
            for m in self.motors:
                m.setPosition(float("inf")); m.setVelocity(0.0)
                m.enableTorqueFeedback(int(self.dt * 1000))
            try:
                self.lidar = self.wb.getDevice("lidar"); self.lidar.enable(int(self.dt * 1000)); self.lidar.enablePointCloud()
            except Exception:  # noqa: BLE001
                self.lidar = None

        # ---- ROS interfaces
        sensor_qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT)
        ns = f"/arcadia/{name.replace('-', '_')}"  # ROS topic names cannot contain hyphens
        self.pub_odom = self.create_publisher(Odometry, f"{ns}/odom", sensor_qos)
        self.pub_imu = self.create_publisher(Imu, f"{ns}/imu", sensor_qos)
        self.pub_batt = self.create_publisher(Battery, f"{ns}/battery", 10)
        self.pub_state = self.create_publisher(State, f"{ns}/state", 10)
        self.pub_diag = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.pub_event = self.create_publisher(Event, "/arcadia/event", 50)
        self.create_subscription(Command, "/arcadia/command", self.on_command, 50)
        self.create_subscription(Twist, f"{ns}/cmd_vel", self.on_cmd_vel, 10)
        self.manual_twist: Twist | None = None
        self.manual_until = 0.0
        self.odom_delay: deque = deque()
        self.diag_timer_t = 0.0
        self.glitch_bias = (0.0, 0.0)
        self.jammed_wheel: int | None = None
        self.tow_follow_target: str | None = None
        self.get_logger().info(f"{name} ({self.robot_type}) online, dt={self.dt*1000:.0f}ms, active={self.active}")
        self._announced = False

    # ------------------------------------------------------------------ ROS callbacks
    def on_cmd_vel(self, msg: Twist) -> None:
        self.manual_twist = msg
        self.manual_until = self.b.sim_time + 0.5

    def on_command(self, msg: Command) -> None:
        if msg.kind == "world_load":
            try:
                world = json.loads(msg.payload_json).get("world") or {}
                me = next((r for r in world.get("robots", []) if r["id"] == self.robot_id), None)
                self.docks = [(i["x"], i["y"]) for i in world.get("infrastructure", []) if str(i.get("kind", "")).endswith("dock")]
                if me:
                    home = next((i for i in world.get("infrastructure", []) if i["id"] == me.get("home")), None)
                    if home:
                        self.home_xy = (home["x"], home["y"])
                    # A world (re)broadcast must never wipe live state: only an idle/offline, healthy
                    # unit takes the power flag from the world config.
                    if self.b.fault is None and self.b.mission is None and self.b.status in ("idle", "offline"):
                        self.active = bool(me.get("active", True))
                        self.b.status = "idle" if self.active else "offline"
            except json.JSONDecodeError:
                pass
            return
        if msg.robot_id != self.robot_id:
            return
        try:
            p = json.loads(msg.payload_json or "{}")
        except json.JSONDecodeError:
            p = {}
        pos = self.position()
        if msg.kind in ("mission", "tow"):
            if msg.kind == "tow":
                p["kind"] = "tow"
            self.b.start_mission(p, self.home_xy)
        elif msg.kind == "cancel":
            self.b.cancel()
        elif msg.kind == "return_home":
            if self.home_xy:
                self.b.start_mission({"mission_id": p.get("mission_id", ""), "kind": "charge",
                                      "waypoints": [{"x": self.home_xy[0], "y": self.home_xy[1], "z": 0.0}]}, self.home_xy)
        elif msg.kind == "chaos":
            self.b.inject(p.get("fault", "motor_jam"), p.get("params", {}), pos[:2])
            if p.get("fault") == "motor_jam" and self.motors:
                self.jammed_wheel = {"front_left": 0, "front_right": 1, "rear_left": 2, "rear_right": 3}.get(
                    p.get("params", {}).get("wheel", "front_left"), 0)
            if p.get("fault") == "sensor_glitch":
                b = float(p.get("params", {}).get("bias_m", 0.8))
                self.glitch_bias = (b, -b * 0.6)
        elif msg.kind == "clear_fault":
            self.b.clear_fault(bool(p.get("recharge", False)))
            self.jammed_wheel = None
            self.glitch_bias = (0.0, 0.0)
        elif msg.kind == "set_active":
            self.active = bool(p.get("active", True))
            self.b.status = "idle" if self.active else "offline"
            if not self.active:
                self.b.mission = None
            self.emit({"kind": "robot_power", "robot_id": self.robot_id, "payload": {"active": self.active}})
        elif msg.kind == "set_irradiance":
            self.irradiance = float(p.get("irradiance", 1.0))
        elif msg.kind == "set_view":
            pass  # handled by the supervisor

    # ------------------------------------------------------------------ helpers
    def position(self) -> tuple[float, float, float]:
        v = self.gps.getValues()
        return (v[0], v[1], v[2])

    def yaw(self) -> float:
        return self.imu.getRollPitchYaw()[2]

    def emit(self, e: dict) -> None:
        msg = Event()
        msg.stamp = self.get_clock().now().to_msg()
        msg.kind = e["kind"]; msg.robot_id = e.get("robot_id", self.robot_id); msg.h3 = e.get("h3", "") or ""
        msg.mission_id = e.get("mission_id", "") or ""; msg.severity = e.get("severity", "INFO")
        msg.payload_json = json.dumps(e.get("payload", {}), separators=(",", ":"))
        self.pub_event.publish(msg)

    # ------------------------------------------------------------------ control
    def drive_ugv(self, target: dict | None, pos, yaw) -> float:
        """Pure pursuit with LiDAR avoidance. Returns commanded linear speed."""
        if self.b.fault and self.b.fault.kind == "stuck_terrain":
            # Bogged down: no traction. Wheels are stopped (a spinning wheel on rigid-body physics
            # would just drive away); the supervisor also pins the chassis until the winch frees it.
            self.set_wheels(0.0, 0.0)
            return 0.0
        if target is None or self.b.immobilised:
            self.set_wheels(0.0, 0.0)
            return 0.0
        tx, ty = target["x"] + self.glitch_bias[0], target["y"] + self.glitch_bias[1]
        dx, dy = tx - pos[0], ty - pos[1]
        dist = math.hypot(dx, dy)
        # A tow approaches another robot: the winch attaches from ~2 m, and the target itself must
        # not be treated as an obstacle during the final approach.
        m = self.b.mission or {}
        approaching_robot = m.get("kind") == "tow" and self.b.tow_phase == 0 and self.b.wp_i >= self.b.pickup_index
        last_wp = self.b.wp_i == len(m.get("waypoints") or []) - 1
        parking = m.get("kind") in ("charge", "tow") and last_wp          # dock pads are 6 x 6 m
        reach = 3.0 if approaching_robot else 2.5 if parking else 0.8     # winch cable / pad / waypoint tolerance
        if dist < reach:
            self.set_wheels(0.0, 0.0)
            self.b.reached(target)
            return 0.0
        desired = math.atan2(dy, dx)
        err = math.atan2(math.sin(desired - yaw), math.cos(desired - yaw))
        # LiDAR obstacle avoidance: steer away from the closest return in the front arc
        # (disabled inside the last few metres so the goal itself cannot repel the robot)
        avoid = 0.0
        if self.lidar is not None and dist > 3.5:
            ranges = self.lidar.getRangeImage()
            n = len(ranges)
            if n:
                fov = self.lidar.getFov()
                best, best_i = 1e9, -1
                for i in range(n):
                    ang = -fov / 2 + fov * i / max(1, n - 1)
                    if abs(ang) < math.radians(50) and 0.15 < ranges[i] < best:
                        best, best_i = ranges[i], i
                if best < 1.6 and best_i >= 0:
                    ang = -fov / 2 + fov * best_i / max(1, n - 1)
                    avoid = (1.6 - best) / 1.6 * (1.0 if ang < 0 else -1.0) * 1.8
        vmax = self.spec["max_speed"]
        if abs(err) > 0.5:
            # Large heading error: pivot in place (skid-steer cannot out-turn forward motion on a heavy chassis)
            v, w = 0.0, math.copysign(1.6, err)
        else:
            v = vmax * (1.0 - abs(err) / 0.5 * 0.6) * min(1.0, dist / 2.0 + 0.3)
            w = 2.0 * err + avoid
        if self.manual_twist and self.b.sim_time < self.manual_until:
            v, w = self.manual_twist.linear.x, self.manual_twist.angular.z
        self.set_wheels(v, w)
        return v

    def set_wheels(self, v: float, w: float) -> None:
        vl = (v - w * AXLE / 2) / WHEEL_RADIUS
        vr = (v + w * AXLE / 2) / WHEEL_RADIUS
        vmax = self.motors[0].getMaxVelocity() if self.motors else 20.0
        vl, vr = max(-vmax, min(vmax, vl)), max(-vmax, min(vmax, vr))
        for i, m in enumerate(self.motors):
            if self.jammed_wheel is not None and i == self.jammed_wheel:
                m.setVelocity(0.0)
                continue
            m.setVelocity(vl if i % 2 == 0 else vr)

    def fly_uav(self, target: dict | None, pos) -> float:
        """Kinematic flight: the UAV proto is a physics-less rigid body moved by a supervisor-free
        trick - propeller motors spin for visuals while the controller sets velocity through a
        virtual thrust (Webots 'Propeller' nodes). To stay light on an iGPU we approximate with a
        linear-velocity command on the propellers' parent via wheels-less kinematics."""
        for p in self.props:
            p.setVelocity(60.0 if self.b.status in ("active", "returning") else 15.0)
        if target is None or self.b.immobilised:
            self._uav_cmd = (0.0, 0.0, 0.0)
            return 0.0
        tx, ty, tz = target["x"], target["y"], target.get("z", self.spec["cruise_alt"]) or self.spec["cruise_alt"]
        dx, dy, dz = tx - pos[0], ty - pos[1], tz - pos[2]
        dist = math.hypot(dx, dy)
        if dist < 0.6 and abs(dz) < 0.6:
            self._uav_cmd = (0.0, 0.0, 0.0)
            self.b.reached(target)
            return 0.0
        vmax = self.spec["max_speed"]
        v = min(vmax, dist * 1.2)
        vx, vy = (dx / dist * v, dy / dist * v) if dist > 1e-6 else (0.0, 0.0)
        vz = max(-2.0, min(2.0, dz * 1.5))
        self._uav_cmd = (vx, vy, vz)
        return v

    # ------------------------------------------------------------------ main loop
    def spin(self) -> None:
        last_odom = 0.0
        last_batt = 0.0
        self._uav_cmd = (0.0, 0.0, 0.0)
        while self.wb.step(int(self.dt * 1000)) != -1 and rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.0)
            pos = self.position()
            yaw = self.yaw()
            if not self._announced and self.b.sim_time > 0.5:  # let publishers match before announcing
                self._announced = True
                self.emit({"kind": "robot_online", "payload": {"type": self.robot_type, "active": self.active,
                                                               "battery_pct": self.b.battery.pct, "x": pos[0], "y": pos[1]}})
            if not self.active:
                if self.motors:
                    self.set_wheels(0.0, 0.0)
                if self.b.sim_time - last_batt >= 0.5:
                    last_batt = self.b.sim_time
                    self.publish_state(0.0)
                    self.publish_diag(pos, 0.0)
                self.b.sim_time += self.dt
                continue
            if self.docks:  # reflex heads for the NEAREST charging dock, not necessarily home
                nearest = min(self.docks, key=lambda d: (d[0] - pos[0]) ** 2 + (d[1] - pos[1]) ** 2)
                self.b.low_battery_rth(nearest)
            else:
                self.b.low_battery_rth(self.home_xy)
            target = self.b.tick(self.dt, pos, self.irradiance)
            speed = self.fly_uav(target, pos) if self.is_uav else self.drive_ugv(target, pos, yaw)
            # torque-coupled battery drain: mechanical power = sum(|torque_i * omega_i|) at the wheels,
            # plus drivetrain losses; clamped so collision spikes cannot empty a pack.
            if self.is_uav:
                torque_w = 380.0 * (speed / self.spec["max_speed"]) ** 1.3 + (90.0 if pos[2] > 0.5 else 0.0)
            elif self.b.immobilised:
                torque_w = 0.0  # electronics only
            else:
                # Skid-steer wheel scrub inflates torque feedback (all four wheels are fixed), so the
                # mechanical term is scaled to a realistic drivetrain draw and clamped.
                # Rolling resistance of a ~180 kg rover at 1.4 m/s is ~250 W; turning scrub adds more.
                mech_w = sum(abs(m.getTorqueFeedback()) * abs(m.getVelocity()) for m in self.motors)
                torque_w = min(mech_w * 0.12, 450.0) + 120.0 * (speed / self.spec["max_speed"])
            if self.b.status != "charging":
                self.b.battery.step(self.dt, torque_w, self.irradiance)
            self.b.motor_temp = min(88.0, self.b.motor_temp + self.dt * 0.9 * (speed / self.spec["max_speed"])) if speed > 0.05 \
                else max(34.0, self.b.motor_temp - self.dt * 0.6)
            if self.b.fault and self.b.fault.kind == "motor_jam":
                self.b.motor_temp = 92.0
            self.b.check_battery_death(pos)
            for e in self.b.drain_events():
                self.emit(e)
            # UAV kinematics: integrate the velocity command (the UAV proto has no physics)
            if self.is_uav and hasattr(self, "_uav_cmd"):
                self.uav_integrate(pos)
            now = self.b.sim_time
            if now - last_odom >= 0.02:
                last_odom = now
                self.publish_odom(pos, yaw, speed)
            if now - last_batt >= 0.5:
                last_batt = now
                self.publish_battery(torque_w)
                self.publish_state(speed)
                self.publish_diag(pos, speed)
        self.destroy_node()

    def uav_integrate(self, pos) -> None:
        """The UAV proto is kinematic (no Physics node) to stay cheap on an iGPU: integrate the
        velocity command and move the node directly (this controller runs with supervisor TRUE)."""
        vx, vy, vz = self._uav_cmd
        if abs(vx) + abs(vy) + abs(vz) < 1e-4 and pos[2] >= 0.29:
            return
        nx, ny, nz = pos[0] + vx * self.dt, pos[1] + vy * self.dt, max(0.3, pos[2] + vz * self.dt)
        self.tr_field.setSFVec3f([nx, ny, nz])
        if abs(vx) + abs(vy) > 1e-3:
            self.rot_field.setSFRotation([0.0, 0.0, 1.0, math.atan2(vy, vx)])
        self.self_node.resetPhysics()

    def publish_odom(self, pos, yaw, speed) -> None:
        msg = Odometry()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"
        msg.child_frame_id = self.robot_id
        msg.pose.pose.position.x, msg.pose.pose.position.y, msg.pose.pose.position.z = pos
        msg.pose.pose.orientation = yaw_to_quat(yaw)
        msg.twist.twist.linear.x = speed
        g = self.gyro.getValues() if self.gyro is not None else (0.0, 0.0, 0.0)
        msg.twist.twist.angular.z = g[2]
        if self.b.latency_ms > 0:  # chaos: network latency -> delayed, lossy odometry
            self.odom_delay.append((time.time() + self.b.latency_ms / 1000.0, msg))
            while self.odom_delay and self.odom_delay[0][0] <= time.time():
                _, m = self.odom_delay.popleft()
                if hash(m.header.stamp.nanosec) % 10 > 3:
                    self.pub_odom.publish(m)
        else:
            self.pub_odom.publish(msg)
        imu = Imu()
        imu.header = msg.header
        imu.orientation = msg.pose.pose.orientation
        a = self.accel.getValues() if self.accel is not None else (0.0, 0.0, 9.81)
        imu.linear_acceleration.x, imu.linear_acceleration.y, imu.linear_acceleration.z = a
        imu.angular_velocity.x, imu.angular_velocity.y, imu.angular_velocity.z = g
        self.pub_imu.publish(imu)

    def publish_battery(self, torque_w: float) -> None:
        msg = Battery()
        msg.stamp = self.get_clock().now().to_msg()
        msg.robot_id = self.robot_id
        msg.pct = self.b.battery.pct
        msg.voltage = self.b.battery.voltage
        msg.power_w = self.b.battery.power_w
        msg.current_a = msg.power_w / max(1.0, msg.voltage)
        msg.solar_in_w = self.spec["solar_skin_w"] * self.irradiance
        msg.charging = self.b.status == "charging"
        self.pub_batt.publish(msg)

    def publish_state(self, speed: float) -> None:
        b = self.b
        m = State()
        m.stamp = self.get_clock().now().to_msg()
        m.robot_id = self.robot_id
        m.type = self.robot_type
        m.status = b.status if self.active else "offline"
        m.active = self.active
        m.mission_id = (b.mission or {}).get("mission_id", "") or ""
        m.fault = b.fault.kind if b.fault else ""
        m.latency_ms = b.latency_ms
        m.motor_temp_c = b.motor_temp
        m.cpu_load = 0.18 + 0.35 * (speed / self.spec["max_speed"]) + (0.3 if b.mission else 0.0)
        self.pub_state.publish(m)

    def publish_diag(self, pos, speed) -> None:
        arr = DiagnosticArray()
        arr.header.stamp = self.get_clock().now().to_msg()
        b = self.b
        def st(name, level, message, **kv):
            s = DiagnosticStatus(name=f"arcadia/{self.robot_id}/{name}", hardware_id=self.robot_id, level=level, message=message)
            s.values = [KeyValue(key=k, value=str(v)) for k, v in kv.items()]
            return s
        OK, WARN, ERR = DiagnosticStatus.OK, DiagnosticStatus.WARN, DiagnosticStatus.ERROR
        temp = b.motor_temp
        motor_level = ERR if (b.fault and b.fault.kind == "motor_jam") else WARN if temp > 75 else OK
        batt_level = ERR if b.battery.pct < 8 else WARN if b.battery.pct < 25 else OK
        net_level = ERR if b.latency_ms > 2000 else WARN if b.latency_ms > 400 else OK
        cpu = 0.18 + 0.35 * (speed / self.spec["max_speed"]) + (0.3 if b.mission else 0.0)
        arr.status = [
            st("motors", motor_level, "jammed" if motor_level == ERR else "nominal", temperature_c=f"{temp:.1f}", torque_feedback=f"{sum(abs(m.getTorqueFeedback()) for m in self.motors):.2f}" if self.motors else "n/a"),
            st("battery", batt_level, "critical" if batt_level == ERR else "low" if batt_level == WARN else "nominal", pct=f"{b.battery.pct:.1f}", voltage=f"{b.battery.voltage:.1f}", power_w=f"{b.battery.power_w:.0f}"),
            st("network", net_level, "degraded" if net_level else "nominal", latency_ms=f"{b.latency_ms:.0f}"),
            st("compute", WARN if cpu > 0.85 else OK, "nominal", cpu_load=f"{cpu:.2f}"),
            st("localisation", WARN if (b.fault and b.fault.kind == "sensor_glitch") else OK, "glitch" if (b.fault and b.fault.kind == "sensor_glitch") else "nominal", x=f"{pos[0]:.2f}", y=f"{pos[1]:.2f}", z=f"{pos[2]:.2f}"),
        ]
        self.pub_diag.publish(arr)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ArcadiaRobot()
    try:
        node.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main(sys.argv)
