"""Layer 4/5: fleet state, operator overrides (click-to-dispatch), dispatch queue control."""
from __future__ import annotations

import h3
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import Farm, HexCell, Infrastructure, Mission, Robot
from app.schemas import Command, DispatchRequest, MissionOut, RobotOut
from app.services import fleet
from app.services.geo import Origin
from app.workers.tasks import dispatch_mission

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("")
async def get_fleet(s: AsyncSession = Depends(get_session)):
    live = await fleet.get_fleet()
    rows = (await s.execute(select(Robot))).scalars().all()
    out = []
    for r in rows:
        d = RobotOut.model_validate(r).model_dump()
        d.update({k: v for k, v in live.get(r.id, {}).items() if k not in ("id",)})
        out.append(d)
    return out


@router.post("/{robot_id}/dispatch", response_model=MissionOut, status_code=201)
async def operator_dispatch(robot_id: str, body: DispatchRequest, s: AsyncSession = Depends(get_session)):
    """Click-to-Dispatch Override: bypasses the automated queue and publishes a waypoint immediately."""
    robot = await s.get(Robot, robot_id)
    if robot is None:
        raise HTTPException(404, "robot not found")
    if not robot.farm_id:
        raise HTTPException(409, "robot is not assigned to an active farm")
    farm = await s.get(Farm, robot.farm_id)
    origin = Origin(farm.centroid_lat, farm.centroid_lng)
    target = None
    if body.target_h3:
        cell = (await s.execute(select(HexCell).where(HexCell.farm_id == farm.id, HexCell.h3_index == body.target_h3))).scalar_one_or_none()
        if cell is None:
            raise HTTPException(404, "target hex is not part of this farm")
        target = {"x": cell.x, "y": cell.y, "h3": cell.h3_index, "lat": cell.lat, "lng": cell.lng}
    elif body.lat is not None and body.lng is not None:
        x, y = origin.to_xy(body.lat, body.lng)
        target = {"x": round(x, 3), "y": round(y, 3), "lat": body.lat, "lng": body.lng,
                  "h3": h3.latlng_to_cell(body.lat, body.lng, farm.h3_resolution)}
    elif body.x is not None and body.y is not None:
        lat, lng = origin.to_latlng(body.x, body.y)
        target = {"x": body.x, "y": body.y, "lat": lat, "lng": lng}
    if target is None:
        raise HTTPException(422, "provide target_h3, lat/lng or x/y")
    z = 6.0 if robot.type == "scout_uav" else 0.0
    # Cancel whatever the queue had planned for this robot
    live = await fleet.get_robot(robot_id) or {}
    for m in (await s.execute(select(Mission).where(Mission.robot_id == robot_id,
                                                    Mission.status.in_(("queued", "dispatched", "running"))))).scalars():
        m.status = "cancelled"
    if live.get("current_mission_id") or live.get("mission_id"):
        await fleet.send_command(Command(kind="cancel", robot_id=robot_id, payload={}))
    kind = body.kind if body.kind in ("goto", "seed", "weed", "harvest", "survey", "charge") else "goto"
    m = Mission(farm_id=farm.id, robot_id=robot_id, kind=kind, source="operator", priority=body.priority,
                target_h3=target.get("h3"), waypoints=[{**target, "z": z}], payload={"crop": None}, note=body.note or "Operator override")
    s.add(m)
    await s.commit()
    await s.refresh(m)
    dispatch_mission.delay(m.id)
    await fleet.update_robot(robot_id, manual_override=True)
    return m


@router.post("/{robot_id}/return-home", response_model=MissionOut, status_code=201)
async def return_home(robot_id: str, s: AsyncSession = Depends(get_session)):
    robot = await s.get(Robot, robot_id)
    if robot is None or not robot.farm_id:
        raise HTTPException(404, "robot not found")
    home = (await s.execute(select(Infrastructure).where(Infrastructure.farm_id == robot.farm_id,
                                                         Infrastructure.slug == (robot.home_dock or "dock-1")))).scalar_one_or_none()
    if home is None:
        raise HTTPException(409, "robot has no home dock")
    for m in (await s.execute(select(Mission).where(Mission.robot_id == robot_id,
                                                    Mission.status.in_(("queued", "dispatched", "running"))))).scalars():
        m.status = "cancelled"
    await fleet.send_command(Command(kind="cancel", robot_id=robot_id))
    m = Mission(farm_id=robot.farm_id, robot_id=robot_id, kind="charge", source="operator", priority=0,
                waypoints=[{"x": home.x, "y": home.y, "z": 0.0, "h3": home.h3_index, "lat": home.lat, "lng": home.lng}],
                payload={"dock": home.slug}, note="Operator recall to dock")
    s.add(m)
    await s.commit()
    await s.refresh(m)
    dispatch_mission.delay(m.id)
    return m


@router.post("/{robot_id}/resume-auto")
async def resume_auto(robot_id: str):
    await fleet.update_robot(robot_id, manual_override=False)
    return {"ok": True}


@router.post("/{robot_id}/power")
async def set_power(robot_id: str, active: bool, s: AsyncSession = Depends(get_session)):
    robot = await s.get(Robot, robot_id)
    if robot is None:
        raise HTTPException(404, "robot not found")
    if active:
        rows = (await s.execute(select(Robot).where(Robot.farm_id == robot.farm_id, Robot.active.is_(True)))).scalars().all()
        from app.core.config import settings
        if robot_id not in {r.id for r in rows} and len(rows) >= settings.max_active_robots:
            raise HTTPException(409, f"active-robot cap ({settings.max_active_robots}) reached - hardware constraint")
    robot.active = active
    robot.status = "idle" if active else "offline"
    await s.commit()
    await fleet.send_command(Command(kind="set_active", robot_id=robot_id, payload={"active": active}))
    await fleet.update_robot(robot_id, active=active, status="idle" if active else "offline")
    return {"ok": True, "active": active}
