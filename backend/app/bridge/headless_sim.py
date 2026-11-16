"""Headless digital twin: a lightweight kinematic fleet simulator.

When the native ROS 2 / Webots stack is not running (e.g. developing the dashboard on a
laptop, CI, or the hardware is busy), the BridgeManager falls back to this module so the
entire platform still behaves end-to-end. It speaks the *same* wire protocol as the Webots
stack (TelemetryFrame / SimEvent / Command) so nothing downstream knows the difference.

Crop ground truth lives in the backend's agronomic world model (app.services.world_engine), as it
does for the Webots stack: this twin only moves robots, decides when the scout UAV has a section in
view (`section_seen`) and reports field work (seeded/fertilised/weeded/sprayed/harvested).
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.schemas import Command, SimEvent, TelemetryFrame

log = logging.getLogger("arcadia.twin")

TelemetrySink = Callable[[TelemetryFrame], Awaitable[None]]
EventSink = Callable[[SimEvent], Awaitable[None]]

TIME_COMPRESSION = 12.0  # battery minutes pass 12x faster than wall-clock so energy matters in a demo
WORK_SPEED = 0.5  # m/s while a tool is engaged on a work pass
WORK_KINDS = ("seed", "fertilise", "weed", "spray", "harvest")
WORK_EVENT = {"seed": "seeded", "fertilise": "fertilised", "weed": "weeded", "spray": "sprayed", "harvest": "harvested"}
SEEN_COOLDOWN_S = 20.0
BROADCAST = ("field_state", "weather", "set_irradiance", "set_view", "world_load")


@dataclass
class Section:
    """Geometry of one bed section (the world model owns its agronomic state)."""
    id: str
    bed: str
    crop: str
    x: float
    y: float
    length: float
    width: float
    seen_t: float = -1e9


@dataclass
class Fault:
    kind: str
    params: dict
    t0: float


@dataclass
class RobotSim:
    id: str
    type: str
    x: float
    y: float
    z: float
    home: str
    spec: dict
    active: bool = True
    heading: float = 0.0
    speed: float = 0.0
    battery_pct: float = 100.0
    status: str = "idle"
    mission: dict | None = None
    wp_i: int = 0
    work_until: float = 0.0
    fault: Fault | None = None
    towing: str | None = None
    tow_phase: int = 0
    motor_temp: float = 34.0
    cpu: float = 0.18
    odometer: float = 0.0
    latency_ms: float = 0.0
    visited: set = field(default_factory=set)

    @property
    def is_uav(self) -> bool:
        return self.type == "scout_uav"


class HeadlessTwin:
    def __init__(self, on_telemetry: TelemetrySink, on_event: EventSink, hz: float = 10.0, telemetry_hz: float = 5.0):
        self.on_telemetry = on_telemetry
        self.on_event = on_event
        self.dt = 1.0 / hz
        self.telemetry_every = max(1, int(round(hz / telemetry_hz)))
        self.world: dict | None = None
        self.sections: dict[str, Section] = {}
        self.hazards: list[dict] = []
        self.net = None
        self.infra: dict[str, dict] = {}
        self.robots: dict[str, RobotSim] = {}
        self.rng = random.Random(7)
        self._task: asyncio.Task | None = None
        self._tick = 0
        self._paused = False
        self.sim_time = 0.0
        self.speed_factor = 1.0

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="headless-twin")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def load_world(self, world: dict) -> None:
        self.world = world
        self.rng = random.Random(world.get("farm", {}).get("seed", 7))
        self.sections = {c["id"]: Section(id=c["id"], bed=c.get("bed", ""), crop=c["crop"], x=c["x"], y=c["y"],
                                          length=c.get("length", 10.0), width=c.get("width", 4.0)) for c in world.get("sections", [])}
        self.wet_tracks = False
        self.hazards = list(world.get("hazards", []))
        self.net = None
        if world.get("tracks"):
            from app.services.track_network import TrackNetwork
            self.net = TrackNetwork(world["tracks"])
        self.infra = {i["id"]: i for i in world["infrastructure"]}
        self.robots = {
            r["id"]: RobotSim(id=r["id"], type=r["type"], x=r["x"], y=r["y"], z=r["z"], home=r["home"],
                              spec=r["spec"], active=r.get("active", True),
                              status="idle" if r.get("active", True) else "offline")
            for r in world["robots"]
        }
        self.sim_time = 0.0
        log.info("Headless twin loaded world: %d sections, %d hazards, %d robots", len(self.sections), len(self.hazards),
                 len(self.robots))

    # ------------------------------------------------------------- commands
    async def handle_command(self, cmd: Command) -> None:
        if cmd.kind == "world_load":
            if cmd.payload.get("world"):
                self.load_world(cmd.payload["world"])
                await self.on_event(SimEvent(kind="world_loaded", payload={"sections": len(self.sections), "source": "twin"}))
            return
        if cmd.kind == "pause":
            self._paused = True
            return
        if cmd.kind == "resume":
            self._paused = False
            return
        if cmd.kind == "set_speed":
            self.speed_factor = float(cmd.payload.get("factor", 1.0))
            return
        if cmd.kind == "weather":
            self.wet_tracks = bool(cmd.payload.get("wet_tracks", False))
            return
        if cmd.kind in BROADCAST and not cmd.robot_id:
            return
        r = self.robots.get(cmd.robot_id or "")
        if r is None:
            log.warning("command for unknown robot %s", cmd.robot_id)
            return
        if cmd.kind == "mission":
            await self._start_mission(r, cmd.payload)
        elif cmd.kind == "cancel":
            await self._end_mission(r, ok=False, reason="cancelled")
        elif cmd.kind == "return_home":
            await self._start_mission(r, {"mission_id": cmd.payload.get("mission_id"), "kind": "charge",
                                          "waypoints": self._dock_route(r)})
        elif cmd.kind == "chaos":
            await self._inject(r, cmd.payload)
        elif cmd.kind == "clear_fault":
            await self._clear_fault(r, recharge=cmd.payload.get("recharge", False))
        elif cmd.kind == "set_active":
            r.active = bool(cmd.payload.get("active", True))
            r.status = "idle" if r.active else "offline"
            if not r.active:
                r.mission = None
                home = self.infra.get(r.home)
                if home:
                    r.x, r.y = home["x"], home["y"]
            await self.on_event(SimEvent(kind="robot_power", robot_id=r.id, payload={"active": r.active}))
        elif cmd.kind == "tow":
            await self._start_mission(r, {**cmd.payload, "kind": "tow"})

    def _nearest_dock_wp(self, r: RobotSim) -> dict:
        docks = [i for i in self.infra.values() if str(i.get("kind", "")).endswith("dock")]
        if not docks:
            return self._home_wp(r)
        d = min(docks, key=lambda i: (i["x"] - r.x) ** 2 + (i["y"] - r.y) ** 2)
        return {"x": d["x"], "y": d["y"], "z": 0.0}

    def _dock_route(self, r: RobotSim) -> list[dict]:
        """Ground units go home over the tracks; the UAV flies straight."""
        wp = self._nearest_dock_wp(r)
        if r.is_uav or self.net is None:
            return [wp]
        pts, _ = self.net.route(r.x, r.y, wp["x"], wp["y"])
        return [{"x": x, "y": y, "z": 0.0} for x, y in pts[1:]] or [wp]

    def _join_tracks(self, r: RobotSim, m: dict) -> dict:
        """Ground units never cut across beds to reach a mission's first waypoint: if it is not right
        here, prepend an on-track route from where the unit actually is (mirrors the controllers)."""
        wps = list(m.get("waypoints") or [])
        if not wps or self.net is None or math.hypot(wps[0]["x"] - r.x, wps[0]["y"] - r.y) < 1.5:
            return m
        pts, _ = self.net.route(r.x, r.y, wps[0]["x"], wps[0]["y"])
        lead = [{"x": x, "y": y, "z": 0.0} for x, y in pts[:-1]]
        if not lead:
            return m
        m = {**m, "waypoints": lead + wps}
        for k in ("pass_from", "pickup_index"):
            if k in m:
                m[k] = int(m[k]) + len(lead)
        return m

    def _home_wp(self, r: RobotSim) -> dict:
        home = self.infra.get(r.home) or next(iter(self.infra.values()))
        return {"x": home["x"], "y": home["y"], "z": 0.0}

    async def _start_mission(self, r: RobotSim, m: dict) -> None:
        if not r.active or r.fault is not None and m.get("kind") != "tow":
            await self.on_event(SimEvent(kind="mission_failed", robot_id=r.id, mission_id=m.get("mission_id"),
                                         severity="WARN", payload={"reason": "robot unavailable", "kind": m.get("kind")}))
            return
        if not r.is_uav:
            m = self._join_tracks(r, m)
        r.mission = m
        r.wp_i = 0
        r.work_until = 0.0
        r.visited = set()
        r.tow_phase = 0
        r.status = "towing" if m.get("kind") == "tow" else "returning" if m.get("kind") == "charge" else "active"
        if m.get("kind") == "tow":
            target = self.robots.get(m.get("target_robot", ""))
            if target is None:
                await self._end_mission(r, ok=False, reason="tow target missing")
                return
            if not m.get("waypoints"):
                m["waypoints"] = [{"x": target.x, "y": target.y, "z": 0.0}, self._home_wp(target)]
                m["pickup_index"] = 0
            m.setdefault("pickup_index", 0)
        await self.on_event(SimEvent(kind="mission_started", robot_id=r.id, mission_id=m.get("mission_id"),
                                     payload={"kind": m.get("kind")}))

    async def _end_mission(self, r: RobotSim, ok: bool, reason: str = "", extra: dict | None = None) -> None:
        m = r.mission
        r.mission = None
        if r.status not in ("fault", "stranded", "offline", "charging"):
            r.status = "idle"
        if m is None:
            return
        payload = {"kind": m.get("kind"), "reason": reason, **(extra or {})}
        await self.on_event(SimEvent(kind="mission_complete" if ok else "mission_failed", robot_id=r.id,
                                     mission_id=m.get("mission_id"), severity="INFO" if ok else "WARN", payload=payload))

    async def _inject(self, r: RobotSim, p: dict) -> None:
        kind = p.get("fault", "motor_jam")
        r.fault = Fault(kind=kind, params=p.get("params", {}), t0=self.sim_time)
        detail = {"fault": kind, "params": r.fault.params, "x": r.x, "y": r.y, "battery_pct": r.battery_pct}
        if kind == "motor_jam":
            r.status = "stranded"
            r.speed = 0.0
            r.motor_temp = 92.0
            detail["motor_temp_c"] = r.motor_temp
        elif kind == "battery_drop":
            r.battery_pct = min(r.battery_pct, float(r.fault.params.get("level_pct", 4.0)))
            r.status = "stranded"
            r.speed = 0.0
            detail["battery_pct"] = r.battery_pct
        elif kind == "network_latency":
            r.latency_ms = float(r.fault.params.get("latency_ms", 1800.0))
            r.status = "fault"
            detail["latency_ms"] = r.latency_ms
        elif kind == "stuck_terrain":
            r.status = "stranded"
            r.speed = 0.0
            r.motor_temp = 70.0
        elif kind == "sensor_glitch":
            r.status = "fault"
        mission_id = r.mission.get("mission_id") if r.mission else None
        if kind in ("motor_jam", "battery_drop", "stuck_terrain"):
            r.mission = None
        await self.on_event(SimEvent(kind="fault_detected", robot_id=r.id, mission_id=mission_id, severity="ERROR",
                                     payload=detail))

    async def _clear_fault(self, r: RobotSim, recharge: bool) -> None:
        r.fault = None
        r.latency_ms = 0.0
        r.motor_temp = 40.0
        if recharge:
            r.status = "charging"
            # Serviced where it was delivered (repair station / dock pad); only units recovered
            # somewhere else are parked at their home dock - mirrors the Webots supervisor.
            if not any((i["x"] - r.x) ** 2 + (i["y"] - r.y) ** 2 < 64.0 for i in self.infra.values()):
                home = self.infra.get(r.home)
                if home:
                    r.x, r.y = home["x"], home["y"]
            await self.on_event(SimEvent(kind="charging_started", robot_id=r.id))
        else:
            r.status = "idle"
        await self.on_event(SimEvent(kind="fault_cleared", robot_id=r.id, payload={"recharged": recharge}))

    # ------------------------------------------------------------- physics-lite loop
    async def _loop(self) -> None:
        last = time.monotonic()
        while True:
            now = time.monotonic()
            real_dt = now - last
            last = now
            if self.world and not self._paused:
                dt = min(real_dt, 0.25) * self.speed_factor
                self.sim_time += dt
                for r in self.robots.values():
                    if r.active:
                        await self._step_robot(r, dt)
                self._tick += 1
                if self._tick % self.telemetry_every == 0:
                    for r in self.robots.values():  # parked units report too: routes start from real poses
                        if r.active or self._tick % (self.telemetry_every * 5) == 0:
                            await self.on_telemetry(self._frame(r))
            await asyncio.sleep(self.dt)

    async def _step_robot(self, r: RobotSim, dt: float) -> None:
        spec = r.spec
        idle_w = 25.0 if r.is_uav else 60.0
        wh = spec.get("battery_wh", 1000)
        if r.status == "charging":
            r.battery_pct = min(100.0, r.battery_pct + dt * (100.0 / 45.0))  # full charge in 45 s sim
            r.speed = 0.0
            if r.battery_pct >= 99.5:
                r.status = "idle"
                await self.on_event(SimEvent(kind="charged", robot_id=r.id, payload={"battery_pct": 100.0}))
            return
        if r.fault and r.fault.kind in ("motor_jam", "battery_drop", "stuck_terrain"):
            r.speed = 0.0
            return
        if r.mission is None:
            r.speed = 0.0
            r.battery_pct = max(0.0, r.battery_pct - dt * idle_w / wh * 100 / 3600 * TIME_COMPRESSION)  # trickle drain
            if not r.is_uav or spec.get("solar_skin_w", 0) == 0:
                return
            r.battery_pct = min(100.0, r.battery_pct + dt * spec["solar_skin_w"] / wh * 100 / 3600 * TIME_COMPRESSION)  # solar skin
            return

        m = r.mission
        if r.battery_pct < 22.0 and m.get("kind") not in ("charge", "tow") and r.status != "returning":
            mid = m.get("mission_id")
            r.mission = None
            await self.on_event(SimEvent(kind="mission_failed", robot_id=r.id, mission_id=mid, severity="WARN",
                                         payload={"reason": "low battery - returning to dock", "kind": m.get("kind")}))
            await self._start_mission(r, {"mission_id": f"rth-{r.id}-{int(self.sim_time)}", "kind": "charge",
                                          "waypoints": self._dock_route(r)})
            return
        wps = m.get("waypoints") or []
        if r.work_until > self.sim_time:
            r.speed = 0.0
            r.cpu = 0.55
            return
        if r.wp_i >= len(wps):
            await self._complete(r)
            return
        wp = wps[r.wp_i]
        tx, ty, tz = wp["x"], wp["y"], wp.get("z", 0.0)
        dx, dy = tx - r.x, ty - r.y
        dist = math.hypot(dx, dy)
        vmax = spec.get("max_speed", 1.0)
        if r.fault and r.fault.kind == "sensor_glitch":
            dx += self.rng.gauss(0, 0.6)
            dy += self.rng.gauss(0, 0.6)
        if dist < 0.4:
            r.x, r.y = tx, ty
            await self._reached(r, wp)
            return
        if m.get("kind") in WORK_KINDS and r.wp_i > int(m.get("pass_from", 10 ** 6)):
            vmax = min(vmax, WORK_SPEED)  # tool engaged on the work pass
        v = min(vmax, dist / max(dt, 1e-3))
        r.heading = math.atan2(dy, dx)
        r.x += math.cos(r.heading) * v * dt
        r.y += math.sin(r.heading) * v * dt
        r.z += (tz - r.z) * min(1.0, dt * 1.5)
        r.speed = v
        r.odometer += v * dt
        # torque-coupled drain (compressed timeline so a mission visibly costs energy)
        drain_w = idle_w + (380.0 if r.is_uav else 330.0) * (v / vmax) ** 1.3  # mirrors the Webots controller
        r.battery_pct = max(0.0, r.battery_pct - dt * drain_w / wh * 100 / 3600 * TIME_COMPRESSION)
        r.motor_temp = min(85.0, r.motor_temp + dt * 0.9 * (v / vmax)) if v > 0 else max(34.0, r.motor_temp - dt * 0.6)
        r.cpu = 0.3 + 0.2 * (v / vmax)
        if r.is_uav and (m.get("kind") == "survey" or m.get("observe")):
            self._observe(r)
        if not r.is_uav and v > 0.2 and r.fault is None and self.wet_tracks:
            for hz in self.hazards:  # waterlogged low spots on the tramlines / headland
                if (hz["x"] - r.x) ** 2 + (hz["y"] - r.y) ** 2 < hz.get("r", 3.5) ** 2 and self.rng.random() < 0.04 * dt:
                    await self._inject(r, {"fault": "stuck_terrain", "params": {"terrain": "waterlogged track", "hazard": hz["id"],
                                                                               "wheel_slip": 0.85}, "source": "terrain"})
                    return
        if r.battery_pct <= 0.5:
            r.fault = Fault(kind="battery_drop", params={"natural": True}, t0=self.sim_time)
            r.status = "stranded"
            mid = m.get("mission_id")
            r.mission = None
            await self.on_event(SimEvent(kind="fault_detected", robot_id=r.id, mission_id=mid, severity="ERROR",
                                         payload={"fault": "battery_drop", "params": {"natural": True}, "x": r.x, "y": r.y,
                                                  "battery_pct": 0.0}))

    def _observe(self, r: RobotSim) -> None:
        """Proximity mock CV: the section is in the scout's footprint -> `section_seen`."""
        rad = r.spec.get("sensor_radius_m", 8.0)
        for c in self.sections.values():
            if (c.x - r.x) ** 2 + (c.y - r.y) ** 2 > (rad + c.length / 2) ** 2:
                continue
            r.visited.add(c.id)
            if self.sim_time - c.seen_t < SEEN_COOLDOWN_S:
                continue
            c.seen_t = self.sim_time
            self._queue_event(SimEvent(kind="section_seen", robot_id=r.id, section=c.id,
                                       mission_id=r.mission.get("mission_id") if r.mission else None,
                                       payload={"section": c.id, "altitude_m": round(r.z, 1)}))

    _pending_events: list[SimEvent] = []

    def _queue_event(self, e: SimEvent) -> None:
        self._pending_events.append(e)
        if len(self._pending_events) == 1:
            asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self._flush_events()))

    async def _flush_events(self) -> None:
        pend, self._pending_events = self._pending_events, []
        for e in pend:
            await self.on_event(e)

    async def _reached(self, r: RobotSim, wp: dict) -> None:
        m = r.mission
        kind = m.get("kind")
        if kind == "tow":
            if r.tow_phase == 0 and r.wp_i >= int(m.get("pickup_index", 0)):
                target = self.robots.get(m.get("target_robot", ""))
                r.towing = target.id if target else None
                r.tow_phase = 1
                r.wp_i += 1
                await self.on_event(SimEvent(kind="tow_attached", robot_id=r.id, mission_id=m.get("mission_id"),
                                             payload={"target": r.towing}))
                r.work_until = self.sim_time + 3.0  # winch time
                return
            r.wp_i += 1
            return
        if kind == "patrol":
            r.wp_i += 1
            if r.wp_i >= len(m.get("waypoints") or []) and m.get("loop"):
                r.wp_i = 0  # loop the route until cancelled or the battery reflex kicks in
                m["_laps"] = int(m.get("_laps", 0)) + 1
            return
        if kind == "charge" and r.wp_i == len(m["waypoints"]) - 1:
            r.wp_i += 1
            return
        r.wp_i += 1

    async def _complete(self, r: RobotSim) -> None:
        m = r.mission
        kind = m.get("kind")
        extra: dict = {}
        if kind in WORK_KINDS:
            c = self.sections.get(m.get("section", "")) or self._nearest_section(r.x, r.y)
            if c:
                await self.on_event(SimEvent(kind=WORK_EVENT[kind], robot_id=r.id, section=c.id, mission_id=m.get("mission_id"),
                                             payload={"section": c.id, "crop": c.crop}))
                extra["section"] = c.id
        elif kind == "tow":
            target = self.robots.get(r.towing or "")
            if target:
                target.x, target.y = r.x, r.y
                await self.on_event(SimEvent(kind="tow_complete", robot_id=r.id, mission_id=m.get("mission_id"),
                                             payload={"target": target.id}))
            r.towing = None
        elif kind == "charge":
            r.status = "charging"
            await self.on_event(SimEvent(kind="charging_started", robot_id=r.id))
        elif kind in ("survey", "patrol"):
            extra["sections_observed"] = len(r.visited)
            if kind == "patrol":
                extra["laps"] = int(m.get("_laps", 0))
        await self._end_mission(r, ok=True, extra=extra)
        if kind == "charge":
            r.status = "charging"

    def _nearest_section(self, x: float, y: float) -> Section | None:
        if not self.sections:
            return None
        return min(self.sections.values(), key=lambda c: (c.x - x) ** 2 + (c.y - y) ** 2)

    def _frame(self, r: RobotSim) -> TelemetryFrame:
        # Towed robot follows the tow vehicle
        for other in self.robots.values():
            if other.towing == r.id and other.tow_phase == 1:
                r.x, r.y = other.x - 1.6 * math.cos(other.heading), other.y - 1.6 * math.sin(other.heading)
        v = 48.0 * (0.85 + 0.15 * r.battery_pct / 100.0) if not r.is_uav else 22.2 * (0.8 + 0.2 * r.battery_pct / 100)
        return TelemetryFrame(
            robot_id=r.id, type=r.type, status=r.status, x=round(r.x, 3), y=round(r.y, 3), z=round(r.z, 3),
            heading=round(r.heading, 4), speed=round(r.speed, 3), battery_pct=round(r.battery_pct, 2),
            battery_v=round(v, 2), motor_temp_c=round(r.motor_temp, 1), cpu_load=round(r.cpu, 2),
            mission_id=r.mission.get("mission_id") if r.mission else None, stamp=time.time(), latency_ms=r.latency_ms,
        )
