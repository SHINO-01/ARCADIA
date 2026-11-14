"""BridgeManager: the single point where the backend meets the simulation.

Modes
  ros       - talk to the native ROS 2 stack through rosbridge (ws://host:9090)
  headless  - run the in-process kinematic twin
  auto      - probe rosbridge; use it if reachable, otherwise headless; keep probing and
              hot-swap to ROS when the native stack comes up.

Responsibilities
  * forward Commands published on Redis CH_COMMANDS to the active sim
  * ingest telemetry -> Redis fleet cache + CH_TELEMETRY fan-out + batched Postgres writes
  * ingest events -> services.events
  * ingest /diagnostics_agg -> fleet diagnostics
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import h3
from sqlalchemy import select

from app.bridge.headless_sim import HeadlessTwin
from app.bridge.rosbridge_client import RosBridgeClient
from app.core.config import settings
from app.core.db import session_scope
from app.core.redis import CH_COMMANDS, CH_TELEMETRY, KEY_BRIDGE_STATUS, get_redis
from app.models import Farm, Telemetry
from app.schemas import Command, SimEvent, TelemetryFrame
from app.services import events, fleet
from app.services.geo import Origin

log = logging.getLogger("arcadia.bridge")

TOPIC_CMD = "/arcadia/command"
TOPIC_TELEMETRY = "/arcadia/telemetry"
TOPIC_EVENT = "/arcadia/event"
TOPIC_DIAG = "/diagnostics_agg"
MSG_CMD = "arcadia_msgs/msg/Command"
MSG_TELEMETRY = "arcadia_msgs/msg/Telemetry"
MSG_EVENT = "arcadia_msgs/msg/Event"
MSG_DIAG = "diagnostic_msgs/msg/DiagnosticArray"


class BridgeManager:
    def __init__(self) -> None:
        self.mode = "starting"
        self.ros = RosBridgeClient(settings.rosbridge_url, settings.bridge_reconnect_s)
        self.twin = HeadlessTwin(self._on_telemetry, self._on_event, hz=settings.headless_tick_hz)
        self.farm_id: str | None = None
        self.origin: Origin | None = None
        self.resolution: int = 12
        self.world: dict | None = None
        self._tasks: list[asyncio.Task] = []
        self._buffer: list[dict] = []
        self._last_fleet_push: dict[str, float] = {}
        self.stats = {"telemetry": 0, "events": 0, "commands": 0, "persisted": 0, "last_telemetry": 0.0}
        self.towed: set[str] = set()
        self.ros.on_connect = self._ros_connected
        self.ros.on_disconnect = self._ros_disconnected

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self._load_active_farm()
        if settings.bridge_mode == "headless":
            await self._use_headless()
        else:
            reachable = await self.ros.try_connect(timeout=3.0)
            if reachable or settings.bridge_mode == "ros":
                self.mode = "ros" if reachable else "ros-connecting"
                self.ros.start()
                if not reachable:
                    log.warning("bridge_mode=ros but rosbridge unreachable; will keep retrying")
            else:
                await self._use_headless()
                self.ros.start()  # keep probing so we can hot-swap once Webots/ROS come up
        self._tasks = [
            asyncio.create_task(self._command_loop(), name="bridge-commands"),
            asyncio.create_task(self._persist_loop(), name="bridge-persist"),
        ]
        await self._publish_status()

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.ros.stop()
        await self.twin.stop()
        await self._flush()

    async def _use_headless(self) -> None:
        self.mode = "headless"
        if self.world:
            self.twin.load_world(self.world)
            await self._reconcile_after_twin_reset()
        self.twin.start()
        log.info("Bridge running in HEADLESS twin mode")

    async def _reconcile_after_twin_reset(self) -> None:
        """The in-memory twin restarts from the world spawn state; bring the durable copies in line:
        reset fleet cache/rows, fail in-flight missions and close incidents that no longer exist."""
        from datetime import datetime, timezone

        from sqlalchemy import update

        from app.models import Incident, Mission, Robot

        robots = []
        for r in self.world.get("robots", []):  # type: ignore[union-attr]
            lat, lng = self.origin.to_latlng(r["x"], r["y"]) if self.origin else (None, None)
            robots.append({"id": r["id"], "type": r["type"], "name": r["name"], "status": "idle" if r["active"] else "offline",
                           "active": r["active"], "battery_pct": 100.0, "x": r["x"], "y": r["y"], "z": r["z"], "lat": lat, "lng": lng,
                           "heading": 0.0, "speed": 0.0, "home": r["home"], "spec": r["spec"], "fault": None,
                           "current_mission_id": None, "mission_id": None, "manual_override": False})
        await fleet.set_fleet(robots)
        now = datetime.now(timezone.utc)
        async with session_scope() as s:
            for r in robots:
                await s.execute(update(Robot).where(Robot.id == r["id"]).values(
                    status=r["status"], active=r["active"], battery_pct=100.0, x=r["x"], y=r["y"], z=r["z"], fault=None,
                    current_mission_id=None))
            await s.execute(update(Mission).where(Mission.status.in_(("queued", "dispatched", "running")))
                            .values(status="failed", completed_at=now, result={"reason": "simulation restarted"}))
            await s.execute(update(Incident).where(Incident.state.in_(("open", "acknowledged", "recovering")))
                            .values(state="resolved", resolved_at=now))
        await fleet.publish_dashboard({"t": "incident_resolved", "robot_id": None})
        log.info("Reconciled fleet/missions/incidents after twin reset")

    async def _ros_connected(self) -> None:
        prev = self.mode
        self.mode = "ros"
        if prev == "headless":
            log.info("rosbridge became reachable - hot-swapping from headless twin to ROS 2")
            await self.twin.stop()
        await self.ros.advertise(TOPIC_CMD, MSG_CMD)
        await self.ros.subscribe(TOPIC_TELEMETRY, MSG_TELEMETRY, self._ros_telemetry, queue_length=10)
        await self.ros.subscribe(TOPIC_EVENT, MSG_EVENT, self._ros_event, queue_length=50)
        await self.ros.subscribe(TOPIC_DIAG, MSG_DIAG, self._ros_diag, throttle_ms=500)
        if self.world:
            await asyncio.sleep(0.5)
            await self._send_world_load()
        await self._publish_status()

    async def _ros_disconnected(self) -> None:
        if settings.bridge_mode == "auto":
            log.warning("rosbridge lost - falling back to headless twin")
            await self._use_headless()
        else:
            self.mode = "ros-connecting"
        await self._publish_status()

    async def _publish_status(self) -> None:
        st = {"mode": self.mode, "ros_connected": self.ros.connected, "farm_id": self.farm_id, "t": time.time(), **self.stats}
        await get_redis().set(KEY_BRIDGE_STATUS, json.dumps(st))
        await fleet.publish_dashboard({"t": "bridge", **st})

    def status(self) -> dict:
        return {"mode": self.mode, "ros_connected": self.ros.connected, "rosbridge_url": settings.rosbridge_url,
                "farm_id": self.farm_id, **self.stats}

    # ------------------------------------------------------------------ world
    async def _load_active_farm(self) -> None:
        """Restore the live farm. Redis is the hot cache; the database is the source of truth, so a
        Redis restart (or `docker compose down`) must not lose the active twin."""
        from app.models import Robot

        fid = await fleet.get_active_farm_id()
        async with session_scope() as s:
            farm = None
            if fid:
                farm = (await s.execute(select(Farm).where(Farm.id == fid))).scalar_one_or_none()
            if farm is None:
                farm = (await s.execute(select(Farm).where(Farm.status == "active").order_by(Farm.updated_at.desc()))).scalars().first()
                if farm is not None:
                    log.info("Restoring active farm %s from the database (Redis had no active farm)", farm.id)
                    await fleet.set_active_farm_id(farm.id)
                    robots = (await s.execute(select(Robot).where(Robot.farm_id == farm.id))).scalars().all()
                    if robots and not await fleet.get_fleet():
                        await fleet.set_fleet([{"id": r.id, "type": r.type, "name": r.name, "status": r.status, "active": r.active,
                                                "battery_pct": r.battery_pct, "x": r.x, "y": r.y, "z": r.z, "lat": r.lat, "lng": r.lng,
                                                "heading": r.heading, "speed": 0.0, "home": r.home_dock} for r in robots])
        if farm and farm.world_config:
            self._set_world(farm)

    def _set_world(self, farm: Farm) -> None:
        self.farm_id = farm.id
        self.world = farm.world_config
        self.origin = Origin(farm.centroid_lat, farm.centroid_lng)
        self.resolution = farm.h3_resolution

    async def activate_farm(self, farm: Farm, robots: list[dict]) -> None:
        self._set_world(farm)
        await fleet.set_active_farm_id(farm.id)
        await fleet.set_fleet(robots)
        if self.mode == "headless":
            self.twin.load_world(self.world)  # type: ignore[arg-type]
            await self._on_event(SimEvent(kind="world_loaded", payload={"source": "twin", "cells": len(self.world["hex"]["cells"])}))
        elif self.ros.connected:
            await self._send_world_load()
        await self._publish_status()

    async def _send_world_load(self) -> None:
        cmd = Command(kind="world_load", payload={"farm_id": self.farm_id, "world": self.world})
        await self.ros.publish(TOPIC_CMD, self._cmd_to_ros(cmd))

    # ------------------------------------------------------------------ commands
    async def _command_loop(self) -> None:
        r = get_redis()
        while True:
            try:
                async with r.pubsub() as ps:
                    await ps.subscribe(CH_COMMANDS)
                    async for msg in ps.listen():
                        if msg.get("type") != "message":
                            continue
                        try:
                            cmd = Command.model_validate_json(msg["data"])
                        except Exception:  # noqa: BLE001
                            log.warning("bad command payload: %s", msg["data"][:200])
                            continue
                        await self.dispatch_command(cmd)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("command loop error: %s; restarting", e)
                await asyncio.sleep(1.0)

    async def dispatch_command(self, cmd: Command) -> bool:
        self.stats["commands"] += 1
        if self.mode == "ros" and self.ros.connected:
            return await self.ros.publish(TOPIC_CMD, self._cmd_to_ros(cmd))
        if self.mode == "headless":
            await self.twin.handle_command(cmd)
            return True
        log.warning("command %s dropped: bridge not connected", cmd.kind)
        return False

    @staticmethod
    def _cmd_to_ros(cmd: Command) -> dict:
        return {"command_id": cmd.command_id or "", "robot_id": cmd.robot_id or "", "kind": cmd.kind,
                "payload_json": json.dumps(cmd.payload, separators=(",", ":"))}

    # ------------------------------------------------------------------ ROS ingress
    async def _ros_telemetry(self, msg: dict) -> None:
        try:
            frame = TelemetryFrame(
                robot_id=msg["robot_id"], type=msg.get("type") or None, status=msg.get("status") or "idle",
                x=msg["x"], y=msg["y"], z=msg.get("z", 0.0), heading=msg.get("heading", 0.0), speed=msg.get("speed", 0.0),
                battery_pct=msg.get("battery_pct", 0.0), battery_v=msg.get("battery_v", 0.0),
                motor_temp_c=msg.get("motor_temp_c", 0.0), cpu_load=msg.get("cpu_load", 0.0),
                mission_id=msg.get("mission_id") or None, stamp=_stamp(msg.get("stamp")), latency_ms=msg.get("latency_ms", 0.0),
            )
        except Exception as e:  # noqa: BLE001
            log.debug("bad telemetry: %s", e)
            return
        await self._on_telemetry(frame)

    async def _ros_event(self, msg: dict) -> None:
        try:
            payload = json.loads(msg.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        evt = SimEvent(kind=msg.get("kind", "unknown"), robot_id=msg.get("robot_id") or None, h3=msg.get("h3") or None,
                       mission_id=msg.get("mission_id") or None, severity=msg.get("severity") or "INFO", payload=payload,
                       stamp=_stamp(msg.get("stamp")))
        await self._on_event(evt)

    async def _ros_diag(self, msg: dict) -> None:
        """diagnostic_aggregator output: /arcadia/<robot>/... -> per-robot OK|WARN|ERROR."""
        levels = {0: "OK", 1: "WARN", 2: "ERROR", 3: "STALE"}
        per_robot: dict[str, dict] = {}
        for st in msg.get("status", []):
            name = st.get("name", "")
            parts = [p for p in name.split("/") if p]
            if len(parts) < 2 or parts[0].lower() != "arcadia":
                continue
            robot = parts[1]
            entry = per_robot.setdefault(robot, {"level": "OK", "items": {}, "message": ""})
            lvl = levels.get(int(st.get("level", 0)) if isinstance(st.get("level"), (int, str, bytes)) else 0, "OK")
            if len(parts) == 2:
                entry["level"] = lvl
                entry["message"] = st.get("message", "")
            else:
                entry["items"][parts[-1]] = {"level": lvl, "message": st.get("message", ""),
                                             "values": {kv["key"]: kv["value"] for kv in st.get("values", [])}}
        for robot, diag in per_robot.items():
            await fleet.update_robot(robot, diagnostics=diag, broadcast=False)
        if per_robot:
            await fleet.publish_dashboard({"t": "diagnostics", "robots": per_robot})

    # ------------------------------------------------------------------ common ingress
    async def _on_telemetry(self, frame: TelemetryFrame) -> None:
        self.stats["telemetry"] += 1
        self.stats["last_telemetry"] = time.time()
        if self.origin is not None:
            frame.lat, frame.lng = self.origin.to_latlng(frame.x, frame.y)
            frame.lat, frame.lng = round(frame.lat, 7), round(frame.lng, 7)
            try:
                frame.h3 = h3.latlng_to_cell(frame.lat, frame.lng, self.resolution)
            except Exception:  # noqa: BLE001
                frame.h3 = None
        data = frame.model_dump()
        data["t"] = "telemetry"
        r = get_redis()
        await r.publish(CH_TELEMETRY, json.dumps(data, separators=(",", ":")))
        # fleet cache: at most ~2 Hz per robot to keep Redis + dashboards light
        now = time.time()
        if now - self._last_fleet_push.get(frame.robot_id, 0.0) > 0.5:
            self._last_fleet_push[frame.robot_id] = now
            status = frame.status
            # A towed robot does not know it is being towed: keep the backend's "towed" status until
            # the tow completes (tow_complete -> clear_fault resets it).
            if status in ("stranded", "fault") and frame.robot_id in self.towed:
                status = "towed"
            await fleet.update_robot(frame.robot_id, broadcast=False, x=frame.x, y=frame.y, z=frame.z, lat=frame.lat,
                                     lng=frame.lng, heading=frame.heading, speed=frame.speed, battery_pct=frame.battery_pct,
                                     status=status, type=frame.type, h3=frame.h3, latency_ms=frame.latency_ms,
                                     mission_id=frame.mission_id, last_seen=now)
        self._buffer.append(data)
        if len(self._buffer) >= settings.telemetry_persist_batch:
            await self._flush()

    async def _on_event(self, evt: SimEvent) -> None:
        self.stats["events"] += 1
        if evt.kind == "tow_attached" and evt.payload.get("target"):
            self.towed.add(evt.payload["target"])
        elif evt.kind in ("tow_complete", "fault_cleared", "robot_online"):
            self.towed.discard(evt.payload.get("target") or evt.robot_id or "")
        await events.handle_event(evt, self.farm_id)

    async def _persist_loop(self) -> None:
        last_status = 0.0
        while True:
            await asyncio.sleep(settings.telemetry_persist_interval_s)
            try:
                await self._flush()
            except Exception as e:  # noqa: BLE001
                log.warning("telemetry persist failed: %s", e)
            if time.time() - last_status > 10.0:  # keep dashboard headers honest across reconnects
                last_status = time.time()
                try:
                    await self._publish_status()
                except Exception as e:  # noqa: BLE001
                    log.debug("status publish failed: %s", e)

    async def _flush(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        rows = [Telemetry(time=datetime.fromtimestamp(d.get("stamp") or time.time(), tz=timezone.utc), robot_id=d["robot_id"],
                          lat=d.get("lat"), lng=d.get("lng"), x=d["x"], y=d["y"], z=d["z"], heading=d["heading"],
                          speed=d["speed"], battery_pct=d["battery_pct"], battery_v=d["battery_v"],
                          motor_temp_c=d["motor_temp_c"], cpu_load=d["cpu_load"], h3_index=d.get("h3"), status=d.get("status"))
                for d in batch]
        # de-duplicate identical (time, robot) PKs from bursts
        seen: set[tuple] = set()
        uniq = []
        for row in rows:
            key = (row.time, row.robot_id)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(row)
        async with session_scope() as s:
            s.add_all(uniq)
        self.stats["persisted"] += len(uniq)


def _stamp(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):  # builtin_interfaces/Time
        return float(v.get("sec", 0)) + float(v.get("nanosec", 0)) / 1e9
    return None


bridge = BridgeManager()
