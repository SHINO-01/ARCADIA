"""Robot behaviour shared by UAV / UGV / AGV controllers: mission execution state machine,
torque-coupled battery model, fault handling. Pure Python (no Webots/ROS imports) so it is
unit-testable and mirrors the headless twin in the backend."""
from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, field

TIME_COMPRESSION = 12.0
WORK_KINDS = ("seed", "weed", "harvest")
WORK_EVENT = {"seed": "seeded", "weed": "weeded", "harvest": "harvested"}
WORK_SPEED = 0.5  # m/s while a tool is engaged on a work pass
BATTERY_RESERVE_PCT = 22.0  # below this a robot abandons its mission and returns to its dock on its own


class TrackGraph:
    """On-board copy of the farm's track network (world_config["track_graph"]): nodes are track
    junctions and bends, edges the straight track legs between them. Ground units use it to reach
    a mission's first waypoint, or a dock on their own, without ever cutting across a bed."""

    def __init__(self, graph: dict | None):
        g = graph or {}
        self.nodes: list[tuple[float, float]] = [(float(x), float(y)) for x, y in g.get("nodes", [])]
        self.edges: list[tuple[int, int]] = [(int(a), int(b)) for a, b in g.get("edges", [])]
        self.adj: dict[int, list[tuple[int, float]]] = {}
        for a, b in self.edges:
            d = math.dist(self.nodes[a], self.nodes[b])
            self.adj.setdefault(a, []).append((b, d))
            self.adj.setdefault(b, []).append((a, d))

    def __bool__(self) -> bool:
        return bool(self.edges)

    def snap(self, x: float, y: float) -> tuple[tuple[float, float], int, float]:
        """Nearest point on any track leg: (point, edge index, distance)."""
        best = ((x, y), -1, math.inf)
        for i, (a, b) in enumerate(self.edges):
            (ax, ay), (bx, by) = self.nodes[a], self.nodes[b]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
            px, py = ax + t * dx, ay + t * dy
            d = math.hypot(x - px, y - py)
            if d < best[2]:
                best = ((px, py), i, d)
        return best

    def route(self, sx: float, sy: float, tx: float, ty: float) -> list[tuple[float, float]]:
        """Shortest on-track polyline from (sx, sy) to (tx, ty), both snapped onto the network.
        Returns the turn points, starting with the start's snap point (empty if no route)."""
        if not self.edges:
            return []
        sp, se, _ = self.snap(sx, sy)
        tp, te, _ = self.snap(tx, ty)
        S, T = -1, -2  # temporary nodes for the snapped start and goal
        pos = {S: sp, T: tp}
        extra: dict[int, list[tuple[int, float]]] = {S: [], T: []}

        def link(n: int, e: int) -> None:
            for m in self.edges[e]:
                d = math.dist(pos[n], self.nodes[m])
                extra[n].append((m, d))
                extra.setdefault(m, []).append((n, d))

        link(S, se)
        link(T, te)
        if se == te:
            d = math.dist(sp, tp)
            extra[S].append((T, d))
            extra[T].append((S, d))
        dist, prev, pq = {S: 0.0}, {S: None}, [(0.0, S)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == T:
                break
            if d > dist.get(u, math.inf):
                continue
            for v, w in self.adj.get(u, []) + extra.get(u, []):
                if d + w < dist.get(v, math.inf):
                    dist[v], prev[v] = d + w, u
                    heapq.heappush(pq, (d + w, v))
        if T not in prev:
            return []
        path = [T]
        while prev[path[-1]] is not None:
            path.append(prev[path[-1]])
        pts = [pos[n] if n < 0 else self.nodes[n] for n in reversed(path)]
        return _turns(pts)


def _turns(pts: list[tuple[float, float]], tol: float = 0.25) -> list[tuple[float, float]]:
    """Keep only the turn points of a polyline (and drop zero-length steps)."""
    out: list[tuple[float, float]] = []
    for p in pts:
        if out and math.dist(p, out[-1]) < 0.05:
            continue
        if len(out) >= 2:
            (ax, ay), (bx, by) = out[-2], out[-1]
            L = math.dist(out[-2], p)
            if L > 1e-6 and abs((p[0] - ax) * (ay - by) - (ax - bx) * (p[1] - ay)) / L <= tol:
                out[-1] = p
                continue
        out.append(p)
    return out


def join_tracks(m: dict, graph: TrackGraph, x: float, y: float, near: float = 1.5) -> dict:
    """Prepend an on-track approach from (x, y) to a ground mission's first waypoint, so a unit
    whose mission was planned from a stale pose still reaches it along the tracks. Shifts the
    indices that refer into the waypoint list."""
    wps = list(m.get("waypoints") or [])
    if not wps or not graph or math.hypot(wps[0]["x"] - x, wps[0]["y"] - y) < near:
        return m
    lead = [{"x": px, "y": py, "z": 0.0} for px, py in graph.route(x, y, wps[0]["x"], wps[0]["y"])[:-1]]
    if not lead:
        return m
    out = {**m, "waypoints": lead + wps}
    for k in ("pass_from", "pickup_index"):
        if k in out:
            out[k] = int(out[k]) + len(lead)
    return out


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
        return self.fault is not None and self.fault.kind in ("motor_jam", "battery_drop", "stuck_terrain")

    @property
    def on_pass(self) -> bool:
        """True while on the work pass of the target bed section (tool engaged)."""
        m = self.mission or {}
        return m.get("kind") in WORK_KINDS and self.wp_i > int(m.get("pass_from", 10 ** 6))

    @property
    def pickup_index(self) -> int:
        return int((self.mission or {}).get("pickup_index", 0))

    # ------------------------------------------------------------- events
    def emit(self, event: str, severity: str = "INFO", **payload) -> None:
        """Queue a domain event. Payload keys are free-form (e.g. kind=<mission kind>)."""
        self.events.append({"kind": event, "robot_id": self.robot_id, "section": str(payload.get("section", "") or ""),
                            "severity": severity,
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
        elif fault == "stuck_terrain":
            self.status, self.motor_temp = "stranded", 70.0
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

    def low_battery_rth(self, plan_route) -> bool:
        """Autonomous reflex: abandon the current task and head for a charging dock.
        plan_route() -> list of (x, y) ending at the dock (ground units: along the tracks)."""
        if self.immobilised or self.status in ("charging", "offline", "returning", "towing"):
            return False
        if self.battery.pct > BATTERY_RESERVE_PCT:
            return False
        route = plan_route()
        if not route:
            return False
        mid = self.mission.get("mission_id", "") if self.mission else ""
        kind = self.mission.get("kind") if self.mission else None
        if self.mission:
            self.mission = None
            self.emit("mission_failed", "WARN", mission_id=mid, reason="low battery - returning to dock", kind=kind)
        self.start_mission({"mission_id": f"rth-{self.robot_id}-{int(self.sim_time)}", "kind": "charge",
                            "waypoints": [{"x": x, "y": y, "z": 0.0} for x, y in route]}, route[-1])
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
            if self.wp_i < self.pickup_index:  # still on the road leg towards the pickup
                self.wp_i += 1
                return
            self.tow_phase = 1
            self.towing = m.get("target_robot")
            self.emit("tow_attached", target=self.towing)
            self.work_until = self.sim_time + 3.0
        elif kind == "patrol":
            self.wp_i += 1
            if self.wp_i >= len(wps) and m.get("loop"):
                self.wp_i = 0
                m["_laps"] = int(m.get("_laps", 0)) + 1
            return
        self.wp_i += 1

    def _complete(self, pos) -> None:
        m = self.mission
        kind = m.get("kind")
        extra: dict = {"kind": kind}
        if kind in WORK_KINDS:
            # The work pass covered the whole bed section (seed/weed from the tramline, harvest down
            # the bed). The supervisor owns ground truth: it listens for these events.
            extra["section"] = m.get("section", "")
            self.emit(WORK_EVENT[kind], section=m.get("section", ""), crop=m.get("crop"))
        elif kind == "tow":
            self.emit("tow_complete", target=self.towing)
            self.towing = None
        elif kind in ("survey", "patrol"):
            extra["sections_observed"] = len(self.visited)
            if kind == "patrol":
                extra["laps"] = int(m.get("_laps", 0))
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
