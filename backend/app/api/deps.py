from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Farm


async def get_farm_or_404(s: AsyncSession, farm_id: str) -> Farm:
    farm = (await s.execute(select(Farm).where(Farm.id == farm_id))).scalar_one_or_none()
    if farm is None:
        raise HTTPException(404, "farm not found")
    return farm
