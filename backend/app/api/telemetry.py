"""Telemetry history + analytics (time-series queries over the hypertables) and the live WebSocket."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bridge.manager import bridge
from app.core.db import get_session
from app.core.redis import CH_DASHBOARD, CH_TELEMETRY, KEY_ENERGY, get_redis
from app.models import EnergyLog, Telemetry, YieldRecord
from app.services import chaos as chaos_svc
from app.services import fleet
from app.services.world_engine import world

log = logging.getLogger("arcadia.ws")
router = APIRouter(tags=["telemetry"])


@router.get("/telemetry/{robot_id}")
async def telemetry_history(robot_id: str, minutes: int = Query(10, le=1440), limit: int = Query(600, le=5000),
                            s: AsyncSession = Depends(get_session)):
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = (await s.execute(select(Telemetry).where(Telemetry.robot_id == robot_id, Telemetry.time >= since)
                            .order_by(Telemetry.time.desc()).limit(limit))).scalars().all()
    return [{"t": r.time.isoformat(), "x": r.x, "y": r.y, "z": r.z, "lat": r.lat, "lng": r.lng, "speed": r.speed,
             "battery_pct": r.battery_pct, "battery_v": r.battery_v, "motor_temp_c": r.motor_temp_c, "cpu_load": r.cpu_load,
             "status": r.status} for r in reversed(rows)]


@router.get("/analytics/yields")
async def yields(s: AsyncSession = Depends(get_session)):
    farm_id = await fleet.get_active_farm_id()
    mine = [YieldRecord.mode == world.mode] + ([YieldRecord.farm_id == farm_id] if farm_id else [])  # current timeline only
    q = select(YieldRecord.crop_type, func.sum(YieldRecord.kg), func.count()).where(*mine).group_by(YieldRecord.crop_type)
    rows = (await s.execute(q)).all()
    recent = (await s.execute(select(YieldRecord).where(*mine).order_by(YieldRecord.harvested_at.desc()).limit(25))).scalars().all()
    return {"by_crop": [{"crop": c, "kg": round(float(kg), 2), "harvests": n} for c, kg, n in rows],
            "total_kg": round(sum(float(kg) for _, kg, _ in rows), 2),
            "recent": [{"section": r.section_id, "crop": r.crop_type, "kg": r.kg, "robot": r.robot_id, "t": r.harvested_at.isoformat()}
                       for r in recent]}


@router.get("/analytics/energy")
async def energy(minutes: int = Query(30, le=1440), s: AsyncSession = Depends(get_session)):
    live = await get_redis().hgetall(KEY_ENERGY)
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = (await s.execute(select(EnergyLog).where(EnergyLog.time >= since).order_by(EnergyLog.time))).scalars().all()
    return {"live": [json.loads(v) for v in live.values()],
            "history": [{"t": r.time.isoformat(), "infra": r.infrastructure_id, "gen": r.generated_kw, "use": r.consumed_kw,
                         "stored": r.stored_kwh, "irr": r.irradiance, "wind": r.wind_ms} for r in rows]}


@router.get("/analytics/fleet")
async def fleet_analytics(s: AsyncSession = Depends(get_session)):
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = (await s.execute(
        select(Telemetry.robot_id, func.count(), func.avg(Telemetry.speed), func.min(Telemetry.battery_pct),
               func.max(Telemetry.motor_temp_c)).where(Telemetry.time >= since).group_by(Telemetry.robot_id))).all()
    return [{"robot_id": r[0], "samples": r[1], "avg_speed": round(float(r[2] or 0), 2), "min_battery": round(float(r[3] or 0), 1),
             "max_motor_temp": round(float(r[4] or 0), 1)} for r in rows]


@router.get("/system/status")
async def system_status(s: AsyncSession = Depends(get_session)):
    db_ok = True
    try:
        await s.execute(text("select 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    r = get_redis()
    try:
        redis_ok = bool(await r.ping())
    except Exception:  # noqa: BLE001
        redis_ok = False
    workers = []
    try:
        from app.workers.celery_app import celery_app
        insp = celery_app.control.inspect(timeout=0.5)
        pong = await asyncio.get_running_loop().run_in_executor(None, insp.ping)
        workers = list((pong or {}).keys())
    except Exception:  # noqa: BLE001
        pass
    return {"db": db_ok, "redis": redis_ok, "celery_workers": workers, "bridge": bridge.status(),
            "dispatch_paused": await fleet.is_dispatch_paused(), "chaos": (await chaos_svc.get_config()).model_dump(),
            "active_farm": await fleet.get_active_farm_id()}


async def ws_telemetry(ws: WebSocket):
    """Live stream: telemetry frames + dashboard events, multiplexed on one socket."""
    await ws.accept()
    r = get_redis()
    ps = r.pubsub()
    await ps.subscribe(CH_TELEMETRY, CH_DASHBOARD)
    # initial snapshot
    await ws.send_text(json.dumps({"t": "snapshot", "fleet": await fleet.get_fleet(), "bridge": bridge.status(),
                                   "dispatch_paused": await fleet.is_dispatch_paused(),
                                   "energy": [json.loads(v) for v in (await r.hgetall(KEY_ENERGY)).values()]}))

    async def reader():
        while True:
            msg = await ws.receive_text()
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            if data.get("t") == "ping":
                await ws.send_text('{"t":"pong"}')

    async def writer():
        async for msg in ps.listen():
            if msg.get("type") == "message":
                await ws.send_text(msg["data"])

    try:
        await asyncio.gather(reader(), writer())
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:  # noqa: BLE001
        log.debug("ws closed: %s", e)
    finally:
        await ps.unsubscribe()
        await ps.aclose()
