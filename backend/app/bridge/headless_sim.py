"""Headless digital twin: a lightweight kinematic fleet simulator.

When the native ROS 2 / Webots stack is not running (e.g. developing the dashboard on a
laptop, CI, or the hardware is busy), the BridgeManager falls back to this module so the
entire platform still behaves end-to-end. It speaks the *same* wire protocol as the Webots
stack (TelemetryFrame / SimEvent / Command) so nothing downstream knows the difference.

Ground truth (crop growth, weeds, moisture) lives here, exactly as it lives in the Webots
Supervisor in the real stack; the backend only learns about it through observations.
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

GROW_S = {"seeded": 70.0, "growing": 110.0}  # accelerated agronomy: seeded -> growing -> ripe
TIME_COMPRESSION = 12.0  # battery minutes pass 12x faster than wall-clock so energy matters in a demo
WORK_S = {"seed": 6.0, "weed": 5.0, "harvest": 8.0}


@dataclass
class Cell:
    h3: str
    x: float
    y: float
    kind: str
    crop: str | None
    state: str = "fallow"
    weeds: bool = False
    moisture: float = 0.5
    health: float = 1.0
    t_state: float = 0.0
    observed_sig: str = ""
    waterlogged: bool = False


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
        self.cells: dict[str, Cell] = {}
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
        self.cells = {
            c["h3"]: Cell(h3=c["h3"], x=c["x"], y=c["y"], kind=c["kind"], crop=c.get("crop"),
                          state="fallow" if c["kind"] == "crop" else "infra", moisture=c.get("moisture", 0.5),
                          waterlogged=bool(c.get("waterlogged", False)))
            for c in world["hex"]["cells"]
        }
        # Hidden ground truth: some cells already carry weeds, some are dry.
        for c in self.cells.values():
            if c.kind == "crop":
                c.weeds = self.rng.random() < 0.12
                c.moisture = min(1.0, max(0.05, c.moisture + self.rng.gauss(0, 0.08)))
        self.infra = {i["id"]: i for i in world["infrastructure"]}
        self.robots = {
            r["id"]: RobotSim(id=r["id"], type=r["type"], x=r["x"], y=r["y"], z=r["z"], home=r["home"],
                              spec=r["spec"], active=r.get("active", True),
                              status="idle" if r.get("active", True) else "offline")
            for r in world["robots"]
        }
        self.sim_time = 0.0
        log.info("Headless twin loaded world: %d cells, %d robots", len(self.cells), len(self.robots))

    # ------------------------------------------------------------- commands
    async def handle_command(self, cmd: Command) -> None:
        if cmd.kind == "world_load":
            if cmd.payload.get("world"):
                self.load_world(cmd.payload["world"])
                await self.on_event(SimEvent(kind="world_loaded", payload={"cells": len(self.cells), "source": "twin"}))
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
                                          "waypoints": [self._home_wp(r)]})
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

    def _home_wp(self, r: RobotSim) -> dict:
        home = self.infra.get(r.home) or next(iter(self.infra.values()))
        return {"x": home["x"], "y": home["y"], "z": 0.0}

    async def _start_mission(self, r: RobotSim, m: dict) -> None:
        if not r.active or r.fault is not None and m.get("kind") != "tow":
            await self.on_event(SimEvent(kind="mission_failed", robot_id=r.id, mission_id=m.get("mission_id"),
                                         severity="WARN", payload={"reason": "robot unavailable", "kind": m.get("kind")}))
            return
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
                self._grow(dt)
                for r in self.robots.values():
                    if r.active:
                        await self._step_robot(r, dt)
                self._tick += 1
                if self._tick % self.telemetry_every == 0:
                    for r in self.robots.values():
                        if r.active:
                            await self.on_telemetry(self._frame(r))
            await asyncio.sleep(self.dt)

    def _grow(self, dt: float) -> None:
        for c in self.cells.values():
            if c.kind != "crop":
                continue
            c.t_state += dt
            if c.state == "seeded" and c.t_state > GROW_S["seeded"]:
                c.state, c.t_state = "growing", 0.0
            elif c.state == "growing" and c.t_state > GROW_S["growing"] * (1.3 - 0.3 * c.moisture):
                c.state, c.t_state = "ripe", 0.0
            elif c.state == "harvested" and c.t_state > 40.0:
                c.state, c.t_state = "fallow", 0.0
            # stochastic weeds & drying
            if not c.weeds and self.rng.random() < 0.00035 * dt * 10:
                c.weeds = True
            c.moisture = max(0.02, c.moisture - 0.00025 * dt)
            if c.weeds:
                c.health = max(0.2, c.health - 0.0006 * dt)
            else:
                c.health = min(1.0, c.health + 0.0003 * dt)

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
                                          "waypoints": [self._nearest_dock_wp(r)]})
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
        if not r.is_uav and v > 0.2 and r.fault is None:
            cell = self._cell_at(r.x, r.y)
            if cell is not None and cell.waterlogged and self.rng.random() < 0.004 * dt * 10:
                await self._inject(r, {"fault": "stuck_terrain", "params": {"terrain": "waterlogged bed", "h3": cell.h3,
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
        rad = r.spec.get("sensor_radius_m", 8.0)
        for c in self.cells.values():
            if c.kind != "crop" or (c.x - r.x) ** 2 + (c.y - r.y) ** 2 > rad * rad:
                continue
            sig = f"{c.state}|{int(c.weeds)}|{round(c.moisture, 1)}|{round(c.health, 1)}"
            if c.h3 in r.visited and c.observed_sig == sig:
                continue
            r.visited.add(c.h3)
            c.observed_sig = sig
            self._queue_event(SimEvent(kind="cell_observed", robot_id=r.id, h3=c.h3,
                                       mission_id=r.mission.get("mission_id") if r.mission else None,
                                       payload={"state": c.state, "weeds": c.weeds, "soil_moisture": round(c.moisture, 3),
                                                "health": round(c.health, 3)}))

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
        if kind in ("seed", "weed", "harvest") and r.wp_i == len(m["waypoints"]) - 1:
            # Arrived at the work cell: spend tool time, then emit the result when done.
            r.work_until = self.sim_time + WORK_S[kind]
            r.wp_i += 1
            m["_pending_work"] = wp
            return
        if kind == "charge" and r.wp_i == len(m["waypoints"]) - 1:
            r.wp_i += 1
            return
        r.wp_i += 1

    async def _complete(self, r: RobotSim) -> None:
        m = r.mission
        kind = m.get("kind")
        extra: dict = {}
        if kind in ("seed", "weed", "harvest"):
            wp = m.get("_pending_work") or (m["waypoints"][-1] if m.get("waypoints") else None)
            c = self.cells.get((wp or {}).get("h3", "")) or self._nearest_cell(r.x, r.y)
            if c:
                if kind == "seed":
                    c.state, c.t_state = "seeded", 0.0
                    await self.on_event(SimEvent(kind="seeded", robot_id=r.id, h3=c.h3, mission_id=m.get("mission_id"),
                                                 payload={"crop": c.crop}))
                elif kind == "weed":
                    c.weeds = False
                    c.health = min(1.0, c.health + 0.3)
                    await self.on_event(SimEvent(kind="weeded", robot_id=r.id, h3=c.h3, mission_id=m.get("mission_id"),
                                                 payload={"health": round(c.health, 3)}))
                elif kind == "harvest":
                    kg = round(self.rng.uniform(0.7, 1.3) * {"wheat": 1.2, "maize": 2.4, "soy": 0.9, "tomato": 3.1,
                                                              "lettuce": 1.5}.get(c.crop or "wheat", 1.0), 2)
                    c.state, c.t_state = "harvested", 0.0
                    extra["kg"] = kg
                    await self.on_event(SimEvent(kind="harvested", robot_id=r.id, h3=c.h3, mission_id=m.get("mission_id"),
                                                 payload={"crop": c.crop, "kg": kg}))
                extra["h3"] = c.h3
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
            extra["cells_observed"] = len(r.visited)
            if kind == "patrol":
                extra["laps"] = int(m.get("_laps", 0))
        await self._end_mission(r, ok=True, extra=extra)
        if kind == "charge":
            r.status = "charging"

    def _cell_at(self, x: float, y: float) -> Cell | None:
        edge = float(self.world["hex"].get("edge_m", 10.0)) if self.world else 10.0
        best, bd = None, (edge * 0.95) ** 2
        for c in self.cells.values():
            d = (c.x - x) ** 2 + (c.y - y) ** 2
            if d < bd:
                best, bd = c, d
        return best

    def _nearest_cell(self, x: float, y: float) -> Cell | None:
        best, bd = None, 1e18
        for c in self.cells.values():
            if c.kind != "crop":
                continue
            d = (c.x - x) ** 2 + (c.y - y) ** 2
            if d < bd:
                best, bd = c, d
        return best

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
