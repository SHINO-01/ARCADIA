"""World endpoints: the simulator's active world config and the agronomic world model of the live farm."""
from __future__ import annotations

import io
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agro.crops import CROPS
from app.core.db import get_session
from app.models import Anomaly, Farm, FarmDaily, WeatherDaily
from app.services import fleet
from app.services.world_engine import ALL_SCENARIOS, world

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


def _need_world():
    if not world.ready:
        raise HTTPException(409, "the world model is not running (activate a farm with a site analysis and crop plan)")


@router.get("/state")
async def state():
    if not world.ready:
        return {"ready": False}
    f = world.forecast or {}
    return {"ready": True, "farm_id": world.farm_id, "clock": world.clock(), "weather": world.now_weather(), "kpi": world.summary,
            "ops": world.ops.summary(), "anomalies": list(world.det.active.values()), "scenarios": world.scenarios,
            "irrigation": world.model.irrigation, "outlook": world.outlook,
            "forecast": {k: v for k, v in f.items() if k not in ("section_yield_t_ha", "section_maturity_day")} or None,
            "climate": {k: world.climate.get(k) for k in ("source", "years", "elevation")} | {
                "annual_rain_mm": round(world.climate["clim"]["annual_rain_mm"]), "annual_tmean": round(world.climate["clim"]["annual_tmean"], 1)},
            "soil": world.soil.summary(), "crop": CROPS[world.model.crop_keys[0]].to_dict(),
            "stages": {name: int((world.model.s["stage"][0] == k).sum()) for k, name in enumerate(
                ["fallow", "sown", "vegetative", "flowering", "grain_fill", "ripe", "harvested"])}}


@router.get("/weather")
async def weather(days: int = Query(60, ge=1, le=730), s: AsyncSession = Depends(get_session)):
    _need_world()
    rows = (await s.execute(select(WeatherDaily).where(WeatherDaily.farm_id == world.farm_id)
                            .order_by(WeatherDaily.date.desc()).limit(days))).scalars().all()
    return {"history": [{"date": r.date, **{k: v for k, v in r.data.items() if k != "probes"}} for r in reversed(rows)],
            "today": world.now_weather(), "outlook": world.outlook}


@router.get("/daily")
async def daily(days: int = Query(120, ge=1, le=730), s: AsyncSession = Depends(get_session)):
    _need_world()
    rows = (await s.execute(select(FarmDaily).where(FarmDaily.farm_id == world.farm_id)
                            .order_by(FarmDaily.date.desc()).limit(days))).scalars().all()
    return [{"date": r.date, **r.data} for r in reversed(rows)]


@router.get("/forecast")
async def forecast():
    _need_world()
    return world.forecast or {}


@router.post("/forecast/refresh")
async def refresh_forecast():
    _need_world()
    await world.refresh_forecast()
    return {k: v for k, v in (world.forecast or {}).items() if k not in ("section_yield_t_ha", "section_maturity_day")}


@router.get("/truth")
async def truth():
    """Ground truth per section (the simulation's hidden state - for model inspection)."""
    _need_world()
    return world.truth()


@router.get("/anomalies")
async def anomalies(active_only: bool = False, limit: int = Query(100, le=1000), s: AsyncSession = Depends(get_session)):
    _need_world()
    q = select(Anomaly).where(Anomaly.farm_id == world.farm_id)
    if active_only:
        q = q.where(Anomaly.active.is_(True))
    rows = (await s.execute(q.order_by(Anomaly.started.desc()).limit(limit))).scalars().all()
    return [{"id": r.id, "key": r.key, "kind": r.kind, "severity": r.severity, "title": r.title, "started": r.started,
             "ended": r.ended, "active": r.active, **{k: r.data.get(k) for k in ("detail", "method", "value", "threshold", "action", "sections")}}
            for r in rows]


class ClockBody(BaseModel):
    day_seconds: float | None = Field(default=None, ge=3, le=3600)
    paused: bool | None = None


@router.post("/clock")
async def set_clock(body: ClockBody):
    _need_world()
    await world.set_clock(body.day_seconds, body.paused)
    return world.clock()


class ScenarioBody(BaseModel):
    kind: Literal["heatwave", "drought", "wet_spell", "frost", "storm", "irrigation_failure", "pest_outbreak"]
    days: int = Field(default=7, ge=1, le=90)
    intensity: float = Field(default=1.0, ge=0.2, le=2.0)
    start_in_days: int = Field(default=0, ge=0, le=60)


@router.get("/scenarios")
async def scenarios():
    return {"catalog": ALL_SCENARIOS, "active": world.scenarios if world.ready else []}


@router.post("/scenarios")
async def add_scenario(body: ScenarioBody):
    _need_world()
    return await world.add_scenario(body.kind, body.days, body.intensity, body.start_in_days)


@router.delete("/scenarios")
async def clear_scenarios():
    _need_world()
    await world.clear_scenarios()
    return {"ok": True}


class IrrigationBody(BaseModel):
    enabled: bool | None = None
    mad: float | None = Field(default=None, ge=0.2, le=0.9)
    capacity_mm: float | None = Field(default=None, ge=1, le=30)


@router.post("/irrigation")
async def irrigation(body: IrrigationBody):
    _need_world()
    for k, v in body.model_dump(exclude_none=True).items():
        world.model.irrigation[k] = v
    return world.model.irrigation


# ----------------------------------------------------------------------------- soil rasters
SOIL_LAYERS = {"clay": "%", "sand": "%", "om": "%", "ph": "", "ec": "dS/m", "taw": "mm/m", "twi": "", "elevation": "m", "ksat": "mm/h"}
_RAMPS = {
    "default": [(0.0, (68, 1, 84)), (0.25, (59, 82, 139)), (0.5, (33, 145, 140)), (0.75, (94, 201, 98)), (1.0, (253, 231, 37))],
    "earth": [(0.0, (36, 30, 26)), (0.35, (112, 82, 48)), (0.7, (196, 160, 98)), (1.0, (247, 232, 190))],
    "water": [(0.0, (247, 236, 210)), (0.4, (150, 196, 214)), (0.75, (52, 120, 190)), (1.0, (16, 42, 110))],
    "salt": [(0.0, (34, 120, 60)), (0.3, (214, 210, 110)), (0.6, (230, 140, 60)), (1.0, (180, 30, 40))],
}
_LAYER_RAMP = {"elevation": "earth", "twi": "water", "taw": "water", "ec": "salt", "ksat": "water"}


def _colour(v: np.ndarray, ramp: list) -> np.ndarray:
    xs = np.array([p for p, _ in ramp])
    cols = np.array([c for _, c in ramp], float)
    out = np.stack([np.interp(v, xs, cols[:, k]) for k in range(3)], axis=-1)
    return out.astype(np.uint8)


@router.get("/soil/layers")
async def soil_layers():
    _need_world()
    sf = world.soil
    o = world.farm
    from app.services.geo import Origin
    org = Origin(o.centroid_lat, o.centroid_lng)
    minx, miny = sf.origin_xy
    ny, nx = sf.shape
    maxx, maxy = minx + nx * sf.cell, miny + ny * sf.cell
    corners = [org.to_latlng(minx, maxy), org.to_latlng(maxx, maxy), org.to_latlng(maxx, miny), org.to_latlng(minx, miny)]
    stats = {}
    for name in SOIL_LAYERS:
        v = sf.layer(name)[sf.mask]
        stats[name] = {"unit": SOIL_LAYERS[name], "min": round(float(np.percentile(v, 2)), 2), "max": round(float(np.percentile(v, 98)), 2)}
    return {"layers": stats, "coordinates": [[lng, lat] for lat, lng in corners], "summary": sf.summary()}


@router.get("/soil/{layer}.png")
async def soil_png(layer: str):
    _need_world()
    if layer not in SOIL_LAYERS:
        raise HTTPException(404, "unknown layer")
    from PIL import Image
    sf = world.soil
    v = sf.layer(layer)
    inside = v[sf.mask]
    lo, hi = np.percentile(inside, 2), np.percentile(inside, 98)
    t = np.clip((v - lo) / max(hi - lo, 1e-9), 0, 1)
    rgb = _colour(t, _RAMPS[_LAYER_RAMP.get(layer, "default")])
    alpha = np.where(sf.mask, 200, 0).astype(np.uint8)
    img = Image.fromarray(np.dstack([rgb, alpha])[::-1], "RGBA")  # row 0 = north
    img = img.resize((img.width * 4, img.height * 4), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=60"})
