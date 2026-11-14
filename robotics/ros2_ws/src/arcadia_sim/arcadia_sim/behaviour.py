"""Robot behaviour shared by UAV / UGV / AGV controllers: mission execution state machine,
torque-coupled battery model, fault handling. Pure Python (no Webots/ROS imports) so it is
unit-testable and mirrors the headless twin in the backend."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

TIME_COMPRESSION = 12.0
WORK_S = {"seed": 6.0, "weed": 5.0, "harvest": 8.0}
BATTERY_RESERVE_PCT = 22.0  # below this a robot abandons its mission and returns to its dock on its own


@dataclass
class Fault:
    kind: str
    params: dict
    t0: float


@dataclass
class BatteryModel:
    capacity_wh: float
    pct: float = 100.0
    idle_w: float = 40.0
    solar_skin_w: float = 0.0
    nominal_v: float = 48.0
    power_w: float = 0.0

    def step(self, dt: float, torque_power_w: float, irradiance: float = 1.0, charging: bool = False) -> None:
        if charging:
            self.pct = min(100.0, self.pct + dt * (100.0 / 45.0))
            self.power_w = -self.capacity_wh / 45.0 * 3600.0 / 100.0
            return
        draw = self.idle_w + torque_power_w - self.solar_skin_w * irradiance
        self.power_w = draw
        self.pct = max(0.0, min(100.0, self.pct - dt * draw / self.capacity_wh * 100.0 / 3600.0 * TIME_COMPRESSION))

    @property
    def voltage(self) -> float:
        return self.nominal_v * (0.82 + 0.18 * self.pct / 100.0)


@dataclass
class Behaviour:
    robot_id: str
    robot_type: str
    spec: dict
    battery: BatteryModel
    status: str = "idle"
    mission: dict | None = None
    wp_i: int = 0
    work_until: float = 0.0
    fault: Fault | None = None
    towing: str | None = None
    tow_phase: int = 0
    pending_work: dict | None = None
    motor_temp: float = 34.0
    latency_ms: float = 0.0
    events: list[dict] = field(default_factory=list)
    sim_time: float = 0.0
    visited: set = field(default_factory=set)

    @property
    def is_uav(self) -> bool:
        return self.robot_type == "scout_uav"

    @property
    def immobilised(self) -> bool:
        return self.fault is not None and self.fault.kind in ("motor_jam", "battery_drop")

    # ------------------------------------------------------------- events
    def emit(self, event: str, severity: str = "INFO", h3: str = "", **payload) -> None:
        """Queue a domain event. Payload keys are free-form (e.g. kind=<mission kind>)."""
        self.events.append({"kind": event, "robot_id": self.robot_id, "h3": h3, "severity": severity,
                            "mission_id": (self.mission or {}).get("mission_id", "") if self.mission else payload.pop("mission_id", ""),
                            "payload": payload})

    def drain_events(self) -> list[dict]:
        ev, self.events = self.events, []
        return ev

    # ------------------------------------------------------------- commands
    def start_mission(self, m: dict, home_xy: tuple[float, float] | None) -> None:
        if self.immobilised and m.get("kind") != "tow":
            self.emit("mission_failed", "WARN", mission_id=m.get("mission_id", ""), reason="robot immobilised", kind=m.get("kind"))
            return
        self.mission = dict(m)
        self.wp_i = 0
        self.work_until = 0.0
        self.pending_work = None
        self.tow_phase = 0
        self.visited = set()
        k = m.get("kind")
        self.status = "towing" if k == "tow" else "returning" if k == "charge" else "active"
        self.emit("mission_started", kind=k)

    def cancel(self) -> None:
        if self.mission:
            mid = self.mission.get("mission_id", "")
            kind = self.mission.get("kind")
            self.mission = None
            self.emit("mission_failed", "WARN", mission_id=mid, reason="cancelled", kind=kind)
        if self.status in ("active", "returning", "towing"):
            self.status = "idle"

    def inject(self, fault: str, params: dict, pos: tuple[float, float]) -> None:
        self.fault = Fault(fault, params, self.sim_time)
        detail = {"fault": fault, "params": params, "x": pos[0], "y": pos[1], "battery_pct": round(self.battery.pct, 1)}
        mid = self.mission.get("mission_id", "") if self.mission else ""
        if fault == "motor_jam":
            self.status, self.motor_temp = "stranded", 92.0
            detail["motor_temp_c"] = 92.0
            self.mission = None
        elif fault == "battery_drop":
            self.battery.pct = min(self.battery.pct, float(params.get("level_pct", 4.0)))
            self.status = "stranded"
            detail["battery_pct"] = self.battery.pct
            self.mission = None
        elif fault == "network_latency":
            self.latency_ms = float(params.get("latency_ms", 1800.0))
            self.status = "fault"
            detail["latency_ms"] = self.latency_ms
        else:
            self.status = "fault"
        self.emit("fault_detected", "ERROR", mission_id=mid, **detail)

    def clear_fault(self, recharge: bool) -> None:
        self.fault = None
        self.latency_ms = 0.0
        self.motor_temp = 40.0
        self.status = "charging" if recharge else "idle"
        if recharge:
            self.emit("charging_started")
        self.emit("fault_cleared", recharged=recharge)

    # ------------------------------------------------------------- per-step logic
    def current_target(self) -> dict | None:
        if not self.mission:
            return None
        wps = self.mission.get("waypoints") or []
        if self.wp_i >= len(wps):
            return None
        return wps[self.wp_i]

    def low_battery_rth(self, home_xy: tuple[float, float] | None) -> bool:
        """Autonomous reflex: abandon the current task and head for the charging dock."""
        if home_xy is None or self.immobilised or self.status in ("charging", "offline", "returning", "towing"):
            return False
        if self.battery.pct > BATTERY_RESERVE_PCT:
            return False
        mid = self.mission.get("mission_id", "") if self.mission else ""
        kind = self.mission.get("kind") if self.mission else None
        if self.mission:
            self.mission = None
            self.emit("mission_failed", "WARN", mission_id=mid, reason="low battery - returning to dock", kind=kind)
        self.start_mission({"mission_id": f"rth-{self.robot_id}-{int(self.sim_time)}", "kind": "charge",
                            "waypoints": [{"x": home_xy[0], "y": home_xy[1], "z": 0.0}]}, home_xy)
        return True

    def tick(self, dt: float, pos: tuple[float, float, float], irradiance: float) -> dict | None:
        """Advance the state machine. Returns the waypoint to drive towards (or None to hold)."""
        self.sim_time += dt
        if self.status == "charging":
            self.battery.step(dt, 0.0, irradiance, charging=True)
            if self.battery.pct >= 99.5:
                self.status = "idle"
                self.emit("charged", battery_pct=100.0)
            return None
        if self.immobilised or self.mission is None:
            return None
        if self.work_until > self.sim_time:
            return None
        tgt = self.current_target()
        if tgt is None:
            self._complete(pos)
            return None
        return tgt

    def reached(self, wp: dict) -> None:
        m = self.mission
        if m is None:
            return
        kind = m.get("kind")
        wps = m.get("waypoints") or []
        last = self.wp_i == len(wps) - 1
        if kind == "tow" and self.tow_phase == 0:
            self.tow_phase = 1
            self.towing = m.get("target_robot")
            self.emit("tow_attached", target=self.towing)
            self.work_until = self.sim_time + 3.0
        elif kind in WORK_S and last:
            self.work_until = self.sim_time + WORK_S[kind]
            self.pending_work = wp
        self.wp_i += 1

    def _complete(self, pos) -> None:
        m = self.mission
        kind = m.get("kind")
        extra: dict = {"kind": kind}
        if kind in WORK_S:
            wp = self.pending_work or ((m.get("waypoints") or [None])[-1]) or {}
            h3 = wp.get("h3", "") or m.get("target_h3", "")
            extra["h3"] = h3
            # The supervisor owns ground truth; it listens to these and mutates the crops.
            self.emit({"seed": "seeded", "weed": "weeded", "harvest": "harvested"}[kind], h3=h3, crop=m.get("crop"))
        elif kind == "tow":
            self.emit("tow_complete", target=self.towing)
            self.towing = None
        elif kind == "survey":
            extra["cells_observed"] = len(self.visited)
        mid = m.get("mission_id", "")
        self.mission = None
        if kind == "charge":
            self.status = "charging"
            self.emit("charging_started")
        elif self.status in ("active", "returning", "towing"):
            self.status = "idle"
        self.emit("mission_complete", mission_id=mid, **extra)

    def check_battery_death(self, pos) -> None:
        if self.battery.pct <= 0.5 and not self.immobilised and self.status not in ("charging", "offline"):
            self.inject("battery_drop", {"natural": True}, pos[:2])
