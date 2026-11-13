"""Convenience endpoints for the simulator side (Webots supervisor fetches the active world here)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import Farm
from app.services import fleet

router = APIRouter(prefix="/world", tags=["world"])


@router.get("/active")
async def active_world(s: AsyncSession = Depends(get_session)):
    fid = await fleet.get_active_farm_id()
    if not fid:
        raise HTTPException(404, "no active farm")
    farm = (await s.execute(select(Farm).where(Farm.id == fid))).scalar_one_or_none()
    if farm is None or not farm.world_config:
        raise HTTPException(404, "active farm has no world config")
    return farm.world_config
