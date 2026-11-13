from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import Incident
from app.schemas import Command, IncidentOut
from app.services import fleet
from app.workers.tasks import recovery_protocol

router = APIRouter(prefix="/incidents", tags=["chaos"])


@router.get("", response_model=list[IncidentOut])
async def list_incidents(state: str | None = None, limit: int = Query(50, le=500), s: AsyncSession = Depends(get_session)):
    q = select(Incident).order_by(Incident.created_at.desc()).limit(limit)
    if state:
        q = q.where(Incident.state.in_(state.split(",")))
    return (await s.execute(q)).scalars().all()


@router.post("/{incident_id}/ack", response_model=IncidentOut)
async def acknowledge(incident_id: str, s: AsyncSession = Depends(get_session)):
    inc = await s.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    if inc.state == "open":
        inc.state = "acknowledged"
        inc.acknowledged_at = datetime.now(timezone.utc)
        await s.commit()
    await fleet.publish_dashboard({"t": "incident", "id": inc.id, "state": inc.state, "robot_id": inc.robot_id})
    return inc


@router.post("/{incident_id}/recover", response_model=IncidentOut)
async def recover(incident_id: str, s: AsyncSession = Depends(get_session)):
    """Operator confirms the recovery: Celery dispatches the Logistics AGV tow protocol."""
    inc = await s.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    if inc.state == "resolved":
        raise HTTPException(409, "already resolved")
    if not inc.requires_tow:
        # soft faults are cleared remotely (reboot the subsystem)
        await fleet.send_command(Command(kind="clear_fault", robot_id=inc.robot_id, payload={"recharge": False}))
        inc.state = "recovering"
        await s.commit()
        return inc
    inc.state = "recovering"
    await s.commit()
    recovery_protocol.delay(incident_id)
    await s.refresh(inc)
    return inc
