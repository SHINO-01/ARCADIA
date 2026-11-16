"""Site analysis for a new farm: what is already on the ground, the local climate and the soil.

Runs once per farm (background task, progress in Redis) and stores everything in `farm.setup`:

  imagery  - satellite tiles over the boundary -> digital image processing: existing roads and
             tracks, buildings, water and tree cover (app.services.imagery)
  climate  - 10 years of ERA5 daily weather at the centroid -> fitted weather generator
  soil     - ISRIC SoilGrids topsoil texture, organic carbon and pH at the centroid
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from app.agro import climate as climate_mod
from app.agro import soil as soil_mod
from app.core.db import session_scope
from app.core.redis import get_redis
from app.models import Farm
from app.services import fleet

log = logging.getLogger("arcadia.site")
_running: set[str] = set()


def key(farm_id: str) -> str:
    return f"arcadia:analysis:{farm_id}"


async def _progress(farm_id: str, step: str, pct: int, detail: str = "", done: bool = False, error: str | None = None) -> None:
    st = {"step": step, "pct": pct, "detail": detail, "done": done, "error": error, "t": time.time()}
    await get_redis().set(key(farm_id), json.dumps(st), ex=3600)
    await fleet.publish_dashboard({"t": "analysis", "farm_id": farm_id, **st})


async def status(farm_id: str) -> dict | None:
    raw = await get_redis().get(key(farm_id))
    return json.loads(raw) if raw else None


async def run(farm_id: str) -> None:
    if farm_id in _running:
        return
    _running.add(farm_id)
    try:
        async with session_scope() as s:
            farm = await s.get(Farm, farm_id)
            lat, lon, boundary, seed = farm.centroid_lat, farm.centroid_lng, farm.boundary, farm.seed
        setup: dict = {}
        await _progress(farm_id, "imagery", 5, "Fetching satellite imagery over the boundary")
        try:
            from app.services import imagery
            setup["imagery"] = await asyncio.to_thread(imagery.analyse_boundary, boundary, lat, lon)
            r = setup["imagery"]
            await _progress(farm_id, "imagery", 35, f"{len(r['roads'])} road segments, {len(r['obstacles'])} obstacles detected")
        except Exception as e:  # noqa: BLE001
            log.exception("imagery analysis failed")
            setup["imagery"] = {"error": str(e), "roads": [], "obstacles": []}
            await _progress(farm_id, "imagery", 35, f"imagery unavailable: {e}")
        await _progress(farm_id, "climate", 40, "Fitting the weather generator to 10 years of ERA5 data")
        setup["climate"] = await asyncio.to_thread(climate_mod.calibrate, lat, lon, seed)
        c = setup["climate"]["clim"]
        await _progress(farm_id, "climate", 70, f"{setup['climate']['source']}: {c['annual_rain_mm']:.0f} mm/yr, {c['annual_tmean']:.1f} degC mean")
        await _progress(farm_id, "soil", 75, "Querying ISRIC SoilGrids")
        base = await asyncio.to_thread(soil_mod.fetch_soilgrids, lat, lon)
        setup["soil_base"] = base or {**soil_mod.DEFAULT_BASE, "source": "default loam (SoilGrids unavailable)"}
        b = setup["soil_base"]
        setup["soil_base"]["texture"] = soil_mod.texture_class(b["sand"], b["clay"])
        setup["analysed_at"] = time.time()
        async with session_scope() as s:
            farm = await s.get(Farm, farm_id)
            farm.setup = {**(farm.setup or {}), **setup}
        await _progress(farm_id, "done", 100, f"{b['texture']} ({b['source']}); ready to optimise the layout", done=True)
    except Exception as e:  # noqa: BLE001
        log.exception("site analysis failed")
        await _progress(farm_id, "error", 100, str(e), done=True, error=str(e))
    finally:
        _running.discard(farm_id)
