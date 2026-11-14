"""Celery tasks. Workers use the sync SQLAlchemy engine and sync Redis."""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text, update

from app.core.db import sync_session
from app.core.redis import KEY_CHAOS_CONFIG, get_sync_redis
from app.models import EnergyLog, Farm, Incident, Infrastructure, Mission, Robot, Telemetry
from app.schemas import Command
from app.services import chaos as chaos_svc
from app.services import dispatcher, energy
from app.services.fleet import (
    sync_active_farm_id, sync_dispatch_paused, sync_get_fleet, sync_publish_dashboard, sync_send_command,
    sync_update_robot,
)
from app.workers.celery_app import celery_app

log = logging.getLogger("arcadia.tasks")
_energy_model = energy.MicrogridModel()
_energy_last = {"t": time.time(), "persist": 0.0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================ dispatch
@celery_app.task(name="arcadia.dispatch.tick")
def auto_dispatch_tick() -> dict:
    """Look at idle robots and the known field state; create and dispatch missions."""
    released = _release_queued_missions()
    if sync_dispatch_paused():
        return {"skipped": "paused", "released": released}
    farm_id = sync_active_farm_id()
    if not farm_id:
        return {"skipped": "no active farm"}
    fleet = sync_get_fleet()
    created = []
    with sync_session() as s:
        farm = s.get(Farm, farm_id)
        if farm is None:
            return {"skipped": "farm missing"}
        robots = s.execute(select(Robot).where(Robot.farm_id == farm_id, Robot.active.is_(True))).scalars().all()
        for robot in robots:
            st = fleet.get(robot.id, {})
            if st.get("status", robot.status) not in ("idle",) or st.get("fault") or st.get("manual_override"):
                continue
            busy = s.execute(select(Mission.id).where(Mission.robot_id == robot.id,
                                                      Mission.status.in_(("queued", "dispatched", "running")))).first()
            if busy:
                continue
            m = dispatcher.plan_for_robot(s, farm, robot, st)
            if m is None:
                continue
            s.add(m)
            s.flush()
            created.append(m.id)
        s.commit()
    for mid in created:
        dispatch_mission.delay(mid)
    return {"created": created}


def _release_queued_missions() -> list[str]:
    """Hand queued missions (e.g. a second tow) to their robot once it is idle."""
    fleet = sync_get_fleet()
    paused = sync_dispatch_paused()
    out = []
    with sync_session() as s:
        queued = s.execute(select(Mission).where(Mission.status == "queued", Mission.robot_id.is_not(None))
                           .order_by(Mission.priority, Mission.created_at)).scalars().all()
        for m in queued:
            if paused and m.source != "recovery":
                continue
            st = fleet.get(m.robot_id or "", {})
            if st.get("status") != "idle" or st.get("fault"):
                continue
            busy = s.execute(select(Mission.id).where(Mission.robot_id == m.robot_id,
                                                      Mission.status.in_(("dispatched", "running")))).first()
            if busy:
                continue
            out.append(m.id)
    for mid in out:
        dispatch_mission.delay(mid)
    return out


@celery_app.task(name="arcadia.dispatch.mission", bind=True, max_retries=3)
def dispatch_mission(self, mission_id: str) -> dict:
    """Send a queued mission to its robot through the bridge."""
    with sync_session() as s:
        m = s.get(Mission, mission_id)
        if m is None or m.status not in ("queued",):
            return {"skipped": True}
        if not m.robot_id:
            return {"skipped": "unassigned"}
        m.status = "dispatched"
        m.dispatched_at = _now()
        m.celery_task_id = self.request.id
        s.execute(update(Robot).where(Robot.id == m.robot_id).values(current_mission_id=m.id))
        s.commit()
        payload = {"mission_id": m.id, "kind": m.kind, "waypoints": m.waypoints or [], "target_h3": m.target_h3,
                   **(m.payload or {})}
        robot_id, kind = m.robot_id, m.kind
    cmd = Command(kind="tow" if kind == "tow" else "mission", robot_id=robot_id, payload=payload)
    sync_send_command(cmd)
    sync_update_robot(robot_id, current_mission_id=mission_id, status="towing" if kind == "tow" else "active")
    sync_publish_dashboard({"t": "mission", "id": mission_id, "status": "dispatched", "robot_id": robot_id, "kind": kind})
    return {"dispatched": mission_id}


@celery_app.task(name="arcadia.dispatch.watchdog")
def mission_watchdog() -> dict:
    """Missions that never report progress (sim restarted, command lost) must not block a robot forever."""
    stale_after = timedelta(seconds=240)
    cutoff = _now() - stale_after
    freed = []
    with sync_session() as s:
        rows = s.execute(select(Mission).where(Mission.status.in_(("dispatched", "running")),
                                               Mission.dispatched_at.is_not(None), Mission.dispatched_at < cutoff)).scalars().all()
        fleet = sync_get_fleet()
        for m in rows:
            st = fleet.get(m.robot_id or "", {})
            # a robot that is still reporting this mission id is genuinely busy (long survey) - leave it
            if st.get("mission_id") == m.id and time.time() - float(st.get("last_seen") or 0) < 30:
                continue
            m.status = "failed"
            m.completed_at = _now()
            m.result = {"reason": "watchdog timeout"}
            if m.robot_id:
                s.execute(update(Robot).where(Robot.id == m.robot_id, Robot.current_mission_id == m.id).values(current_mission_id=None))
                freed.append(m.robot_id)
        s.commit()
    for rid in set(freed):
        st = sync_get_fleet().get(rid, {})
        if st.get("status") in ("active", "returning", "towing"):
            sync_update_robot(rid, status="idle", current_mission_id=None, mission_id=None)
    return {"timed_out": len(freed)}


# ============================================================================ chaos
@celery_app.task(name="arcadia.chaos.tick")
def chaos_tick() -> dict:
    """Beat runs every 10 s; the engine fires at its own configured interval/probability."""
    cfg = chaos_svc.sync_get_config()
    if not cfg.enabled:
        return {"skipped": "disabled"}
    r = get_sync_redis()
    last = r.get("arcadia:chaos_last")
    if last is None:  # first tick after boot: arm the timer, let the fleet settle first
        r.set("arcadia:chaos_last", time.time())
        return {"skipped": "armed"}
    if time.time() - float(last) < cfg.interval_s:
        return {"skipped": "interval"}
    r.set("arcadia:chaos_last", time.time())
    rng = random.Random()
    if rng.random() > cfg.probability:
        return {"skipped": "dice"}
    fleet = sync_get_fleet()
    targets = chaos_svc.eligible_targets(fleet)
    if not targets:
        return {"skipped": "no targets"}
    robot_id = rng.choice(targets)
    cmd = chaos_svc.build_fault_command(robot_id, None, None, rng, allowed=list(cfg.kinds), robot_type=fleet[robot_id].get("type"))
    sync_send_command(cmd)
    sync_publish_dashboard({"t": "chaos", "robot_id": robot_id, "fault": cmd.payload["fault"], "params": cmd.payload["params"]})
    return {"injected": cmd.payload, "robot": robot_id}


# ============================================================================ recovery
@celery_app.task(name="arcadia.recovery.protocol")
def recovery_protocol(incident_id: str) -> dict:
    """Dispatch the Logistics AGV to winch a stranded unit back to its Renewable Charging Dock."""
    with sync_session() as s:
        inc = s.get(Incident, incident_id)
        if inc is None or inc.state == "resolved":
            return {"skipped": "resolved"}
        stranded = s.get(Robot, inc.robot_id)
        agv = s.execute(select(Robot).where(Robot.type == "logistics_agv", Robot.farm_id == inc.farm_id)).scalars().first()
        if stranded is None or agv is None:
            return {"error": "no AGV in fleet"}
        home = s.execute(select(Infrastructure).where(Infrastructure.farm_id == inc.farm_id,
                                                      Infrastructure.slug == (stranded.home_dock or "dock-1"))).scalar_one_or_none()
        fleet = sync_get_fleet()
        st = fleet.get(stranded.id, {})
        sx, sy = float(st.get("x", stranded.x)), float(st.get("y", stranded.y))
        wps = [{"x": sx, "y": sy, "z": 0.0}]
        if home is not None:
            wps.append({"x": home.x, "y": home.y, "z": 0.0, "h3": home.h3_index, "lat": home.lat, "lng": home.lng})
        agv_busy = s.execute(select(Mission.id).where(Mission.robot_id == agv.id,
                                                      Mission.status.in_(("dispatched", "running")))).first() is not None
        m = Mission(farm_id=inc.farm_id, robot_id=agv.id, kind="tow", source="recovery", priority=0, waypoints=wps,
                    payload={"target_robot": stranded.id, "incident_id": inc.id, "dropoff": home.slug if home else None},
                    note=f"Tow {stranded.name} to {home.slug if home else 'dock'}" + (" (queued behind current tow)" if agv_busy else ""))
        s.add(m)
        s.flush()
        inc.state = "recovering"
        inc.recovery_mission_id = m.id
        agv.active = True
        agv.status = agv.status if agv_busy else "idle"
        s.commit()
        mission_id, agv_id = m.id, agv.id
    if agv_busy:  # the dispatcher tick hands queued missions to robots as they become idle
        sync_publish_dashboard({"t": "incident", "id": incident_id, "state": "recovering", "recovery_mission_id": mission_id})
        return {"mission": mission_id, "queued": True}
    # Power the AGV up (it idles in storage, powered down, to respect the active-robot cap).
    sync_send_command(Command(kind="set_active", robot_id=agv_id, payload={"active": True}))
    sync_update_robot(agv_id, active=True, status="idle")
    sync_publish_dashboard({"t": "incident", "id": incident_id, "state": "recovering", "recovery_mission_id": mission_id})
    dispatch_mission.apply_async(args=[mission_id], countdown=1.0)
    return {"mission": mission_id}


# ============================================================================ energy
@celery_app.task(name="arcadia.energy.tick")
def energy_tick() -> dict:
    farm_id = sync_active_farm_id()
    if not farm_id:
        return {"skipped": "no farm"}
    now = time.time()
    dt = min(now - _energy_last["t"], 10.0)
    _energy_last["t"] = now
    with sync_session() as s:
        infra = [{"slug": i.slug, "kind": i.kind, "capacity_kw": i.capacity_kw, "storage_kwh": i.storage_kwh,
                  "stored_kwh": i.stored_kwh, "id": i.id}
                 for i in s.execute(select(Infrastructure).where(Infrastructure.farm_id == farm_id)).scalars().all()]
    fleet = sync_get_fleet()
    readings = energy.tick(infra, fleet, dt, _energy_model)
    sync_publish_dashboard({"t": "energy", "docks": readings})
    if now - _energy_last["persist"] > 10.0 and readings:
        _energy_last["persist"] = now
        by_slug = {i["slug"]: i["id"] for i in infra}
        with sync_session() as s:
            for rd in readings:
                s.add(EnergyLog(time=_now(), infrastructure_id=by_slug[rd["slug"]], generated_kw=rd["generated_kw"],
                                consumed_kw=rd["consumed_kw"], stored_kwh=rd["stored_kwh"], irradiance=rd["irradiance"],
                                wind_ms=rd["wind_ms"]))
                s.execute(update(Infrastructure).where(Infrastructure.id == by_slug[rd["slug"]]).values(stored_kwh=rd["stored_kwh"]))
            s.commit()
    return {"docks": len(readings)}


# ============================================================================ persistence
@celery_app.task(name="arcadia.persist.fleet_snapshot")
def fleet_snapshot() -> dict:
    fleet = sync_get_fleet()
    if not fleet:
        return {"n": 0}
    with sync_session() as s:
        for rid, st in fleet.items():
            vals = {k: st[k] for k in ("status", "battery_pct", "x", "y", "z", "heading", "lat", "lng", "active", "fault")
                    if k in st}
            if "diagnostics" in st:
                vals["diagnostics"] = st["diagnostics"]
            vals["last_seen"] = _now()
            s.execute(update(Robot).where(Robot.id == rid).values(**vals))
        s.commit()
    return {"n": len(fleet)}


@celery_app.task(name="arcadia.persist.retention")
def retention() -> dict:
    from app.core.config import settings
    cutoff = _now() - timedelta(days=settings.telemetry_retention_days)
    with sync_session() as s:
        n = s.execute(delete(Telemetry).where(Telemetry.time < cutoff)).rowcount
        s.execute(delete(EnergyLog).where(EnergyLog.time < cutoff))
        s.commit()
    return {"deleted": n}
