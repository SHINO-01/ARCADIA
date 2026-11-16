"""Domain-event handler: everything the simulation tells us flows through here.

The sim (Webots supervisor + robot controllers, or the headless twin) emits SimEvents. This
module turns them into durable state: hex-cell observations, mission lifecycle, incidents,
yields, and fleet status updates, then fans a compact version out to dashboards.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.core.db import session_scope
from app.models import FieldSection, Incident, Mission, Robot, YieldRecord
from app.schemas import Command, SimEvent
from app.services import fleet
from app.services.field import YIELD_KG_M2

log = logging.getLogger("arcadia.events")

FAULT_TITLES = {
    "motor_jam": "Mechanical failure: drive motor jammed",
    "battery_drop": "Electrical fault: battery voltage collapse",
    "network_latency": "Network degradation: telemetry latency spike",
    "sensor_glitch": "Sensor fault: localisation glitch",
    "stuck_terrain": "Terrain entrapment: wheels slipping, unit immobilised",
}
TOW_REQUIRED = {"motor_jam", "battery_drop", "stuck_terrain"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def handle_event(evt: SimEvent, farm_id: str | None) -> None:
    kind = evt.kind
    try:
        if kind == "section_observed":
            await _section_observed(evt, farm_id)
        elif kind in ("seeded", "weeded", "harvested"):
            await _section_worked(evt, farm_id)
        elif kind == "harvest_result":
            await _harvest_result(evt, farm_id)
        elif kind == "mission_started":
            await _mission_status(evt, "running")
        elif kind == "mission_complete":
            await _mission_status(evt, "done")
        elif kind == "mission_failed":
            await _mission_status(evt, "failed")
        elif kind == "fault_detected":
            await _fault_detected(evt, farm_id)
        elif kind == "fault_cleared":
            await _fault_cleared(evt)
        elif kind == "tow_attached":
            await fleet.update_robot(evt.payload.get("target", ""), status="towed")
        elif kind == "tow_complete":
            await _tow_complete(evt)
        elif kind == "charging_started":
            await fleet.update_robot(evt.robot_id or "", status="charging")
        elif kind == "charged":
            await fleet.update_robot(evt.robot_id or "", status="idle")
        elif kind == "world_loaded":
            log.info("Simulation loaded world for farm %s (%s)", farm_id, evt.payload)
        elif kind == "robot_power":
            active = bool(evt.payload.get("active", True))
            await fleet.update_robot(evt.robot_id or "", active=active, status="idle" if active else "offline")
            async with session_scope() as s:
                await s.execute(update(Robot).where(Robot.id == evt.robot_id).values(active=active, status="idle" if active else "offline"))
        elif kind == "robot_online":
            await _robot_online(evt)
    except Exception:  # never let one bad event kill the bridge loop
        log.exception("Failed handling event %s", evt.model_dump())
    await fleet.publish_dashboard({"t": "event", "event": evt.model_dump()})


async def _robot_online(evt: SimEvent) -> None:
    """A controller (re)booted: anything we believed it was doing is gone. Fail in-flight missions,
    close its incidents and reset it to a clean idle state so the dispatcher can use it again."""
    if not evt.robot_id:
        return
    active = bool(evt.payload.get("active", True))
    # Where the controller actually is (e.g. a powered-down AGV parked in storage): every later
    # route for this unit is planned from here, so a stale position must never survive a boot.
    pose = {k: float(evt.payload[k]) for k in ("x", "y") if evt.payload.get(k) is not None}
    async with session_scope() as s:
        await s.execute(update(Mission).where(Mission.robot_id == evt.robot_id,
                                              Mission.status.in_(("queued", "dispatched", "running")))
                        .values(status="failed", completed_at=_now(), result={"reason": "robot controller restarted"}))
        await s.execute(update(Incident).where(Incident.robot_id == evt.robot_id,
                                               Incident.state.in_(("open", "acknowledged", "recovering")))
                        .values(state="resolved", resolved_at=_now()))
        await s.execute(update(Robot).where(Robot.id == evt.robot_id).values(
            status="idle" if active else "offline", active=active, fault=None, current_mission_id=None,
            battery_pct=float(evt.payload.get("battery_pct", 100.0)), **pose))
    await fleet.update_robot(evt.robot_id, status="idle" if active else "offline", active=active, fault=None,
                             current_mission_id=None, mission_id=None, manual_override=False,
                             battery_pct=float(evt.payload.get("battery_pct", 100.0)), **pose)
    await fleet.publish_dashboard({"t": "incident_resolved", "robot_id": evt.robot_id})
    log.info("robot %s online (%s)", evt.robot_id, evt.payload.get("type"))


async def _apply_section(farm_id: str, section_id: str, values: dict) -> tuple[FieldSection | None, str | None]:
    """Update a bed section. Returns (section after the update, its state before it)."""
    async with session_scope() as s:
        sec = (await s.execute(select(FieldSection).where(FieldSection.farm_id == farm_id, FieldSection.id == section_id))).scalar_one_or_none()
        if sec is None:
            return None, None
        before = sec.state
        for k, v in values.items():
            setattr(sec, k, v)
        await s.flush()
        s.expunge(sec)
        return sec, before


async def _section_observed(evt: SimEvent, farm_id: str | None) -> None:
    sid = evt.payload.get("section")
    if not sid or not farm_id:
        return
    p = evt.payload
    values: dict = {"last_survey_at": _now()}
    for src, dst in (("state", "state"), ("weeds", "weeds"), ("moisture", "moisture"), ("soil_moisture", "moisture"), ("health", "health")):
        if src in p:
            values[dst] = p[src]
    sec, _ = await _apply_section(farm_id, sid, values)
    if sec is not None:
        await fleet.publish_dashboard({"t": "section", "id": sid, "state": sec.state, "weeds": sec.weeds,
                                       "moisture": sec.moisture, "health": sec.health, "surveyed": True})


async def _section_worked(evt: SimEvent, farm_id: str | None) -> None:
    sid = evt.payload.get("section")
    if not sid or not farm_id:
        return
    values: dict = {}
    if evt.kind == "seeded":
        values = {"state": "seeded"}
    elif evt.kind == "weeded":
        values = {"weeds": False, "health": min(1.0, float(evt.payload.get("health", 1.0)))}
    elif evt.kind == "harvested":
        values = {"state": "harvested"}
    sec, _ = await _apply_section(farm_id, sid, values)
    if sec is None:
        return
    msg = {"t": "section", "id": sid, "state": sec.state, "weeds": sec.weeds, "moisture": sec.moisture, "health": sec.health}
    if evt.kind == "harvested" and "was" in evt.payload:  # the headless twin is its own ground truth
        msg.update(await _record_yield(farm_id, sec, evt.payload, evt.robot_id))
    await fleet.publish_dashboard(msg)


async def _harvest_result(evt: SimEvent, farm_id: str | None) -> None:
    """Webots supervisor ground truth for a harvest pass: what state the crop was really in."""
    sid = evt.payload.get("section")
    if not sid or not farm_id:
        return
    async with session_scope() as s:
        sec = (await s.execute(select(FieldSection).where(FieldSection.farm_id == farm_id, FieldSection.id == sid))).scalar_one_or_none()
        if sec is None:
            return
        s.expunge(sec)
    out = await _record_yield(farm_id, sec, evt.payload, evt.robot_id)
    await fleet.publish_dashboard({"t": "section", "id": sid, **out})


async def _record_yield(farm_id: str, sec: FieldSection, p: dict, robot_id: str | None) -> dict:
    """Only a ripe crop yields; an early cut (operator override) clears the bed for nothing."""
    was = p.get("was")
    if was != "ripe":
        kg = 0.0
    elif p.get("kg") is not None:
        kg = float(p["kg"])
    else:
        kg = round(YIELD_KG_M2.get(sec.crop, 0.8) * sec.length * sec.width, 1)
    if kg > 0:
        async with session_scope() as s:
            s.add(YieldRecord(farm_id=farm_id, section_id=sec.id, crop_type=sec.crop, kg=kg, robot_id=robot_id))
    log.info("harvest of %s (%s): crop was %s -> %.1f kg", sec.id, sec.crop, was, kg)
    return {"was": was, "kg": kg}


async def _mission_status(evt: SimEvent, status: str) -> None:
    if not evt.mission_id:
        return
    values: dict = {"status": status}
    if status == "running":
        values["dispatched_at"] = _now()
    if status in ("done", "failed"):
        values["completed_at"] = _now()
        values["result"] = evt.payload or {}
    async with session_scope() as s:
        await s.execute(update(Mission).where(Mission.id == evt.mission_id).values(**values))
        if status in ("done", "failed") and evt.robot_id:
            await s.execute(update(Robot).where(Robot.id == evt.robot_id).values(current_mission_id=None))
    if evt.robot_id:
        if status in ("done", "failed"):
            robot = await fleet.get_robot(evt.robot_id) or {}
            new_status = "idle" if robot.get("status") in (None, "active", "returning", "towing") else robot.get("status")
            await fleet.update_robot(evt.robot_id, current_mission_id=None, status=new_status)
            # A finished tow -> power the Logistics AGV back down to respect the active-robot cap.
            if evt.payload.get("kind") == "tow" and robot.get("type") == "logistics_agv":
                await fleet.send_command(Command(kind="set_active", robot_id=evt.robot_id, payload={"active": False}))
        else:
            await fleet.update_robot(evt.robot_id, current_mission_id=evt.mission_id, status="active")
    await fleet.publish_dashboard({"t": "mission", "id": evt.mission_id, "status": status, "robot_id": evt.robot_id,
                                   "result": evt.payload})


async def _fault_detected(evt: SimEvent, farm_id: str | None) -> None:
    if not evt.robot_id:
        return
    fault = evt.payload.get("fault", "unknown")
    requires_tow = fault in TOW_REQUIRED
    async with session_scope() as s:
        existing = await s.execute(
            select(Incident).where(Incident.robot_id == evt.robot_id, Incident.kind == fault,
                                   Incident.state.in_(("open", "acknowledged", "recovering")))
        )
        inc = existing.scalar_one_or_none()
        if inc is None:
            inc = Incident(
                farm_id=farm_id, robot_id=evt.robot_id, kind=fault,
                severity="ERROR" if requires_tow else "WARN", title=FAULT_TITLES.get(fault, f"Fault: {fault}"),
                detail=evt.payload, requires_tow=requires_tow,
            )
            s.add(inc)
            await s.flush()
        inc_id = inc.id
        await s.execute(update(Robot).where(Robot.id == evt.robot_id).values(
            status="stranded" if requires_tow else "fault", fault=evt.payload,
            current_mission_id=None if requires_tow else Robot.current_mission_id))
        if evt.mission_id and requires_tow:  # soft faults degrade the robot but the mission carries on
            await s.execute(update(Mission).where(Mission.id == evt.mission_id, Mission.status.in_(("dispatched", "running")))
                            .values(status="failed", completed_at=_now(), result={"reason": fault}))
    if requires_tow:
        await fleet.update_robot(evt.robot_id, status="stranded", fault=evt.payload, current_mission_id=None)
    else:
        await fleet.update_robot(evt.robot_id, status="fault", fault=evt.payload)
    await fleet.publish_dashboard({"t": "incident", "id": inc_id, "robot_id": evt.robot_id, "kind": fault,
                                   "requires_tow": requires_tow, "state": "open", "title": FAULT_TITLES.get(fault, fault),
                                   "detail": evt.payload})


async def _fault_cleared(evt: SimEvent) -> None:
    if not evt.robot_id:
        return
    async with session_scope() as s:
        await s.execute(update(Robot).where(Robot.id == evt.robot_id).values(status="idle", fault=None))
        await s.execute(update(Incident).where(Incident.robot_id == evt.robot_id,
                                               Incident.state.in_(("open", "acknowledged", "recovering")))
                        .values(state="resolved", resolved_at=_now()))
    await fleet.update_robot(evt.robot_id, status="idle", fault=None)
    await fleet.publish_dashboard({"t": "incident_resolved", "robot_id": evt.robot_id})


async def _tow_complete(evt: SimEvent) -> None:
    target = evt.payload.get("target")
    if target:
        # Repair at the dock: tell the sim to clear the fault and recharge.
        await fleet.send_command(Command(kind="clear_fault", robot_id=target, payload={"recharge": True}))
        await fleet.update_robot(target, status="charging")
