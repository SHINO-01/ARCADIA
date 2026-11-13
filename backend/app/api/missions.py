from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import Mission
from app.schemas import Command, MissionOut
from app.services import fleet

router = APIRouter(prefix="/missions", tags=["dispatch"])


@router.get("", response_model=list[MissionOut])
async def list_missions(status: str | None = None, limit: int = Query(50, le=500), s: AsyncSession = Depends(get_session)):
    q = select(Mission).order_by(Mission.created_at.desc()).limit(limit)
    if status:
        q = q.where(Mission.status.in_(status.split(",")))
    return (await s.execute(q)).scalars().all()


@router.post("/{mission_id}/cancel", response_model=MissionOut)
async def cancel_mission(mission_id: str, s: AsyncSession = Depends(get_session)):
    m = await s.get(Mission, mission_id)
    if m is None:
        raise HTTPException(404, "mission not found")
    if m.status in ("done", "failed", "cancelled"):
        return m
    m.status = "cancelled"
    await s.commit()
    if m.robot_id:
        await fleet.send_command(Command(kind="cancel", robot_id=m.robot_id, payload={"mission_id": m.id}))
        await fleet.update_robot(m.robot_id, current_mission_id=None, status="idle")
    await s.refresh(m)
    return m


@router.get("/queue/state")
async def queue_state():
    return {"paused": await fleet.is_dispatch_paused()}


@router.post("/queue/pause")
async def pause_queue():
    await fleet.set_dispatch_paused(True)
    return {"paused": True}


@router.post("/queue/resume")
async def resume_queue():
    await fleet.set_dispatch_paused(False)
    return {"paused": False}
