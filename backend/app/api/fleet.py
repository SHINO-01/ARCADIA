"""Layer 4/5: fleet state, operator overrides (click-to-dispatch), dispatch queue control."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import Farm, FieldSection, Mission, Robot
from app.schemas import Command, DispatchRequest, MissionOut, RobotOut, RouteRequest
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
    live = await fleet.get_robot(robot_id) or {}
    rx, ry = float(live.get("x", robot.x or 0)), float(live.get("y", robot.y or 0))
    z = 6.0 if robot.type == "scout_uav" else 0.0
    kind = body.kind if body.kind in ("goto", "seed", "weed", "harvest", "survey", "charge") else "goto"
    if robot.type == "scout_uav" and kind in ("seed", "weed", "harvest"):
        kind = "survey"  # the scout carries cameras, not tools: it inspects the section instead
    payload: dict = {}
    target_section = body.target_section
    from app.services.dispatcher import ground_route, section_mission
    sec = None
    if body.target_section:
        sec = (await s.execute(select(FieldSection).where(FieldSection.farm_id == farm.id, FieldSection.id == body.target_section))).scalar_one_or_none()
        if sec is None:
            raise HTTPException(404, "section not found")
    if sec is not None and robot.type != "scout_uav" and kind in ("seed", "weed", "harvest"):
        # seed/weed: tramline pass beside the bed; harvest: pass down the bed itself
        wps, pass_from, _ = section_mission(farm, sec, rx, ry, kind)
        payload = {"section": sec.id, "bed": sec.bed_id, "crop": sec.crop, "pass_from": pass_from}
    else:
        if sec is not None:
            tx, ty = sec.x, sec.y
        elif body.lat is not None and body.lng is not None:
            tx, ty = origin.to_xy(body.lat, body.lng)
        elif body.x is not None and body.y is not None:
            tx, ty = body.x, body.y
        else:
            raise HTTPException(422, "provide target_section, lat/lng or x/y")
        if robot.type == "scout_uav":
            lat, lng = origin.to_latlng(tx, ty)
            wps = [{"x": round(tx, 3), "y": round(ty, 3), "z": z, "lat": lat, "lng": lng}]
        else:  # ground units never leave the tracks: the target snaps to the nearest track point
            wps, _ = ground_route(farm, rx, ry, tx, ty)
            if kind in ("survey", "seed", "weed", "harvest"):  # work needs a section; without one it is a move
                kind = "goto"
    if not wps:
        raise HTTPException(409, "no route on the track network")
    # Cancel whatever the queue had planned for this robot
    for m in (await s.execute(select(Mission).where(Mission.robot_id == robot_id,
                                                    Mission.status.in_(("queued", "dispatched", "running"))))).scalars():
        m.status = "cancelled"
    if live.get("current_mission_id") or live.get("mission_id"):
        await fleet.send_command(Command(kind="cancel", robot_id=robot_id, payload={}))
    m = Mission(farm_id=farm.id, robot_id=robot_id, kind=kind, source="operator", priority=body.priority,
                target_section=target_section if payload else None, waypoints=wps, payload=payload, note=body.note or "Operator override")
    s.add(m)
    await s.commit()
    await s.refresh(m)
    dispatch_mission.delay(m.id)
    await fleet.update_robot(robot_id, manual_override=True)
    return m


@router.post("/view")
async def set_view(follow: str = "", mode: str = "pan"):
    """3D twin camera: follow a robot (Webots Viewpoint.follow) or return to the farm overview."""
    await fleet.send_command(Command(kind="set_view", payload={"follow": follow or "", "mode": mode}))
    await fleet.publish_dashboard({"t": "view", "follow": follow or None})
    return {"ok": True, "follow": follow or None}


async def _route_waypoints(s: AsyncSession, robot_id: str, body: RouteRequest) -> tuple[Robot, Farm, list[dict]]:
    """Operator route -> mission waypoints. The UAV flies the clicks directly (6 m above the crop);
    ground units follow the tracks leg by leg (each click snaps onto the nearest track)."""
    robot = await s.get(Robot, robot_id)
    if robot is None or not robot.farm_id:
        raise HTTPException(404, "robot not found")
    farm = await s.get(Farm, robot.farm_id)
    origin = Origin(farm.centroid_lat, farm.centroid_lng)
    z = 6.0 if robot.type == "scout_uav" else 0.0
    pts = [origin.to_xy(w.lat, w.lng) if w.lat is not None and w.lng is not None else (w.x, w.y) for w in body.waypoints]
    wps: list[dict] = []
    if robot.type == "scout_uav":
        for x, y in pts:
            lat, lng = origin.to_latlng(x, y)
            wps.append({"x": round(x, 3), "y": round(y, 3), "z": z, "lat": lat, "lng": lng})
    else:
        from app.services.dispatcher import ground_route
        live0 = await fleet.get_robot(robot_id) or {}
        cx, cy = float(live0.get("x", robot.x or 0)), float(live0.get("y", robot.y or 0))
        legs = list(pts) + ([pts[0]] if body.loop and len(pts) > 1 else [])
        for x, y in legs:
            leg, _ = ground_route(farm, cx, cy, x, y)
            wps += leg
            if leg:
                cx, cy = leg[-1]["x"], leg[-1]["y"]
        if not wps:
            raise HTTPException(409, "no route on the track network")
    return robot, farm, wps


@router.post("/{robot_id}/route/preview")
async def preview_route(robot_id: str, body: RouteRequest, s: AsyncSession = Depends(get_session)):
    """The path a route would really take (for the map), without dispatching anything."""
    robot, _, wps = await _route_waypoints(s, robot_id, body)
    live = await fleet.get_robot(robot_id) or {}
    start = [live.get("lng", robot.lng), live.get("lat", robot.lat)]
    coords = ([start] if start[0] is not None and robot.type != "scout_uav" else []) + [[w["lng"], w["lat"]] for w in wps]
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"ground": robot.type != "scout_uav", "waypoints": len(wps)}}


@router.post("/{robot_id}/route", response_model=MissionOut, status_code=201)
async def operator_route(robot_id: str, body: RouteRequest, s: AsyncSession = Depends(get_session)):
    """RTS-style route: ordered waypoints (shift+right-click on the map), optionally looped as a patrol."""
    robot, farm, wps = await _route_waypoints(s, robot_id, body)
    for m in (await s.execute(select(Mission).where(Mission.robot_id == robot_id,
                                                    Mission.status.in_(("queued", "dispatched", "running"))))).scalars():
        m.status = "cancelled"
    await fleet.send_command(Command(kind="cancel", robot_id=robot_id, payload={}))
    kind = "patrol" if body.loop or body.kind == "patrol" else body.kind
    m = Mission(farm_id=farm.id, robot_id=robot_id, kind=kind, source="operator", priority=1, waypoints=wps,
                payload={"loop": bool(body.loop), "observe": robot.type == "scout_uav"},
                note=body.note or (f"Patrol route · {len(wps)} waypoints" + (" (loop)" if body.loop else "")))
    s.add(m)
    await s.commit()
    await s.refresh(m)
    dispatch_mission.delay(m.id)
    await fleet.update_robot(robot_id, manual_override=True)
    return m


@router.post("/{robot_id}/stop")
async def stop_robot(robot_id: str, s: AsyncSession = Depends(get_session)):
    """RTS 'stop': cancel whatever the unit is doing and hold position (stays under manual control)."""
    for m in (await s.execute(select(Mission).where(Mission.robot_id == robot_id,
                                                    Mission.status.in_(("queued", "dispatched", "running"))))).scalars():
        m.status = "cancelled"
    await s.commit()
    await fleet.send_command(Command(kind="cancel", robot_id=robot_id, payload={}))
    await fleet.update_robot(robot_id, manual_override=True, current_mission_id=None, mission_id=None, status="idle")
    return {"ok": True}


@router.post("/{robot_id}/return-home", response_model=MissionOut, status_code=201)
async def return_home(robot_id: str, s: AsyncSession = Depends(get_session)):
    robot = await s.get(Robot, robot_id)
    if robot is None or not robot.farm_id:
        raise HTTPException(404, "robot not found")
    live = await fleet.get_robot(robot_id) or {}
    rx, ry = float(live.get("x", robot.x or 0.0)), float(live.get("y", robot.y or 0.0))
    from app.core.db import sync_session
    from app.services.dispatcher import nearest_dock
    import asyncio
    def _pick():
        with sync_session() as ss:
            farm_row = ss.get(Farm, robot.farm_id)
            return nearest_dock(ss, farm_row, robot.type, rx, ry)
    picked = await asyncio.get_running_loop().run_in_executor(None, _pick)
    if picked is None:
        raise HTTPException(409, "farm has no charging dock")
    home, route_wps, _length = picked
    for m in (await s.execute(select(Mission).where(Mission.robot_id == robot_id,
                                                    Mission.status.in_(("queued", "dispatched", "running"))))).scalars():
        m.status = "cancelled"
    await fleet.send_command(Command(kind="cancel", robot_id=robot_id))
    m = Mission(farm_id=robot.farm_id, robot_id=robot_id, kind="charge", source="operator", priority=0,
                waypoints=route_wps, payload={"dock": home.slug}, note=f"Operator recall to nearest dock {home.slug}")
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
