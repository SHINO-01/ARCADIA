"""Layer 1 - The Ground: farms (boundary), controlled-traffic layout planning, world config, activation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_farm_or_404
from app.bridge.manager import bridge
from app.core.db import get_session
from app.core.redis import get_redis
from app.models import Farm, FieldSection, Incident, Infrastructure, Mission, Robot
from app.schemas import CropPlanRequest, FarmCreate, FarmOut, InfrastructureOut, PlanRequest
from app.services import fleet
from app.services.field import MAX_FIELD_AREA_M2, field_from_geojson, field_of_farm
from app.services.geo import Origin
from app.services.layout import LayoutParams
from app.services.planner import plan_infrastructure
from app.services.world_builder import build_world_config

router = APIRouter(prefix="/farms", tags=["farms"])


@router.get("", response_model=list[FarmOut])
async def list_farms(s: AsyncSession = Depends(get_session)):
    return (await s.execute(select(Farm).order_by(Farm.created_at.desc()))).scalars().all()


@router.post("", response_model=FarmOut, status_code=201)
async def create_farm(body: FarmCreate, s: AsyncSession = Depends(get_session)):
    try:
        fld = field_from_geojson(body.boundary)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if fld.area_m2 < 2_000:
        raise HTTPException(422, f"{fld.area_m2:.0f} m² is too small for a headland, beds and tramlines")
    if fld.area_m2 > MAX_FIELD_AREA_M2:
        raise HTTPException(422, f"{fld.area_m2 / 10_000:.0f} ha is too large for the local sim (max {MAX_FIELD_AREA_M2 / 10_000:.0f} ha)")
    farm = Farm(name=body.name, boundary=body.boundary, centroid_lat=fld.origin.lat, centroid_lng=fld.origin.lng,
                area_m2=round(fld.area_m2, 1), seed=body.seed)
    s.add(farm)
    await s.commit()
    await s.refresh(farm)
    return farm


@router.get("/{farm_id}", response_model=FarmOut)
async def get_farm(farm_id: str, s: AsyncSession = Depends(get_session)):
    return await get_farm_or_404(s, farm_id)


@router.delete("/{farm_id}", status_code=204)
async def delete_farm(farm_id: str, s: AsyncSession = Depends(get_session)):
    farm = await get_farm_or_404(s, farm_id)
    if await fleet.get_active_farm_id() == farm_id:
        await fleet.set_active_farm_id(None)
        await fleet.set_fleet([])
        from app.services.world_engine import world as world_engine
        await world_engine.load(None)
    await s.execute(delete(Robot).where(Robot.farm_id == farm_id))
    await s.delete(farm)
    await s.commit()


@router.get("/{farm_id}/infrastructure", response_model=list[InfrastructureOut])
async def get_infrastructure(farm_id: str, s: AsyncSession = Depends(get_session)):
    await get_farm_or_404(s, farm_id)
    return (await s.execute(select(Infrastructure).where(Infrastructure.farm_id == farm_id))).scalars().all()


@router.post("/{farm_id}/plan", response_model=FarmOut)
async def plan_farm(farm_id: str, body: PlanRequest, s: AsyncSession = Depends(get_session)):
    """Controlled-traffic layout (beds, tramlines, headland, alleys) + K-Means docks, storage and a
    repair station in the yard + world-config payload. Re-planning repositions beds and tracks."""
    farm = await get_farm_or_404(s, farm_id)
    fld = field_of_farm(farm)
    seed = body.seed if body.seed is not None else farm.seed
    plan_c = farm.crop_plan or {}
    params = LayoutParams(bed_width=body.bed_width, lane_width=body.lane_width, headland_width=body.headland_width,
                          section_length=body.section_length, alley_spacing=body.alley_spacing, orientation=body.orientation,
                          shuffle_crops=body.shuffle_crops, seed=seed, use_roads=body.use_roads,
                          crop=plan_c.get("crop", "maize"), rotation_crop=plan_c.get("rotation_crop") or "")
    import asyncio as _asyncio
    try:
        report = await _asyncio.to_thread(plan_infrastructure, fld, body.k_docks, body.k_storage, seed, params, farm.setup)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await s.execute(delete(Infrastructure).where(Infrastructure.farm_id == farm_id))
    await s.execute(delete(FieldSection).where(FieldSection.farm_id == farm_id))
    origin = fld.origin
    world = build_world_config(farm.id, farm.name, fld, report, seed)
    moist = {w["id"]: w["moisture"] for w in world["sections"]}
    for sec in report.layout.sections:
        lat, lng = origin.to_latlng(sec.x, sec.y)
        s.add(FieldSection(farm_id=farm_id, id=sec.id, bed_id=sec.bed_id, crop=sec.crop, state="fallow", weeds=False,
                           moisture=moist.get(sec.id, 0.5), health=1.0, x=sec.x, y=sec.y, lat=lat, lng=lng, length=sec.length, width=sec.width,
                           angle=sec.angle, polygon=sec.polygon, passes=sec.passes))
    for site in report.sites:
        s.add(Infrastructure(farm_id=farm_id, slug=site.slug, kind=site.kind, lat=site.lat, lng=site.lng,
                             x=site.x, y=site.y, cluster_size=site.cluster_size, capacity_kw=site.capacity_kw,
                             storage_kwh=site.storage_kwh, stored_kwh=round(site.storage_kwh * 0.65, 1),
                             meta={"access": site.access}))
    farm.plan = report.to_dict()
    farm.world_config = world
    if farm.crop_plan:  # section ids changed with the new layout
        farm.crop_plan = {**farm.crop_plan, "sections": {sec.id: sec.crop for sec in report.layout.sections}}
    farm.status = "planned" if farm.status != "active" else "active"
    await s.commit()
    await s.refresh(farm)
    if farm.status == "active":  # re-planning a live farm: push the new world to the sim
        await bridge.activate_farm(farm, await _robots_state(s, farm))
        from app.services.world_engine import world as world_engine
        await world_engine.load(farm.id, fresh=True)  # new beds: a new season
    return farm


# ----------------------------------------------------------------------------- site analysis & crops
@router.post("/{farm_id}/analyze")
async def analyze(farm_id: str, s: AsyncSession = Depends(get_session)):
    """Start the site analysis (satellite imagery, ERA5 climate, SoilGrids soil) in the background."""
    await get_farm_or_404(s, farm_id)
    import asyncio as _asyncio

    from app.services import site_analysis
    _asyncio.create_task(site_analysis.run(farm_id))
    return {"started": True}


@router.get("/{farm_id}/analysis")
async def analysis(farm_id: str, s: AsyncSession = Depends(get_session)):
    farm = await get_farm_or_404(s, farm_id)
    from app.services import site_analysis
    st = await site_analysis.status(farm_id)
    setup = farm.setup or {}
    img = setup.get("imagery") or {}
    out = {"status": st, "analysed": bool(setup.get("climate"))}
    if setup.get("climate"):
        c = setup["climate"]
        out["climate"] = {"source": c["source"], "years": c.get("years"), "elevation": c.get("elevation"),
                          "annual_rain_mm": round(c["clim"]["annual_rain_mm"]), "annual_tmean": round(c["clim"]["annual_tmean"], 1),
                          "monthly_rain_prob": [round(x, 2) for x in c["p01"]]}
    if setup.get("soil_base"):
        out["soil"] = setup["soil_base"]
    if img:
        out["imagery"] = {k: img.get(k) for k in ("source", "zoom", "gsd_m", "size_px", "veg_cover_pct", "preview_corners", "error")}
        out["imagery"]["roads"] = [{k: r[k] for k in ("lnglat", "width_m", "length_m", "confidence", "inside", "distance_m")} for r in img.get("roads", [])]
        out["imagery"]["obstacles"] = [{k: o[k] for k in ("kind", "lnglat", "area_m2", "inside")} for o in img.get("obstacles", [])]
    return out


@router.get("/{farm_id}/imagery.jpg")
async def imagery_preview(farm_id: str, s: AsyncSession = Depends(get_session)):
    farm = await get_farm_or_404(s, farm_id)
    jpg = ((farm.setup or {}).get("imagery") or {}).get("preview_jpg")
    if not jpg:
        raise HTTPException(404, "no imagery analysis yet")
    import base64

    from fastapi.responses import Response
    return Response(base64.b64decode(jpg), media_type="image/jpeg")


@router.get("/{farm_id}/crops")
async def crop_options(farm_id: str, refresh: bool = False, s: AsyncSession = Depends(get_session)):
    """Monte Carlo season simulation of every catalogue crop on this farm's soils and climate."""
    farm = await get_farm_or_404(s, farm_id)
    setup = farm.setup or {}
    if not setup.get("climate") or not farm.world_config:
        raise HTTPException(409, "run the site analysis and plan the layout first")
    if setup.get("crop_choice") and not refresh:
        return setup["crop_choice"]
    rows = (await s.execute(select(FieldSection).where(FieldSection.farm_id == farm_id))).scalars().all()
    from datetime import date as _date

    from app.agro.forecast import crop_choice
    from app.services.world_engine import build_world
    import asyncio as _asyncio
    _, _, model, _ = build_world(farm, list(rows))
    res = await _asyncio.to_thread(crop_choice, model, setup["climate"], _date.today(), 12, farm.seed)
    farm.setup = {**setup, "crop_choice": res}
    await s.commit()
    return res


@router.post("/{farm_id}/crop")
async def set_crop(farm_id: str, body: CropPlanRequest, s: AsyncSession = Depends(get_session)):
    """The season's crop (optionally a rotation block). Real farms grow one crop per field."""
    from app.agro.crops import CROPS, CROP_HEIGHT_M_ALL
    from app.agro.forecast import next_sowing
    farm = await get_farm_or_404(s, farm_id)
    if body.crop not in CROPS or (body.rotation_crop and body.rotation_crop not in CROPS):
        raise HTTPException(422, f"crop must be one of {list(CROPS)}")
    rows = (await s.execute(select(FieldSection).where(FieldSection.farm_id == farm_id))).scalars().all()
    beds = sorted({r.bed_id for r in rows}, key=lambda b: (len(b), b))
    second = set(beds[len(beds) // 2:]) if body.rotation_crop and body.rotation_crop != body.crop else set()
    crop_of = {}
    for r in rows:
        r.crop = body.rotation_crop if r.bed_id in second else body.crop
        crop_of[r.id] = r.crop
    if farm.world_config:
        wc = dict(farm.world_config)
        wc["sections"] = [{**sec, "crop": crop_of.get(sec["id"], body.crop), "crop_height": CROP_HEIGHT_M_ALL[crop_of.get(sec["id"], body.crop)]}
                          for sec in wc["sections"]]
        farm.world_config = wc
    start = body.start_date or max(_date_today(), next_sowing(body.crop, farm.centroid_lat, _date_today()) - _td(days=7)).isoformat()
    farm.crop_plan = {"crop": body.crop, "rotation_crop": body.rotation_crop or None, "sections": crop_of, "start_date": start}
    await s.commit()
    await s.refresh(farm)
    if farm.status == "active":  # a new crop plan starts a new season
        from app.services.world_engine import world as world_engine
        await world_engine.load(farm.id, fresh=True)
    return {"crop_plan": farm.crop_plan}


def _date_today():
    from datetime import date as _date
    return _date.today()


def _td(**kw):
    from datetime import timedelta
    return timedelta(**kw)


@router.get("/{farm_id}/layout")
async def get_layout(farm_id: str, s: AsyncSession = Depends(get_session)):
    """Physical field layout as GeoJSON: bed sections (polygons, live state), tracks (buffered to
    their real width), yard pads and waterlogged hazards."""
    farm = await get_farm_or_404(s, farm_id)
    if not farm.world_config or farm.world_config.get("schema", 0) < 3:
        return {"type": "FeatureCollection", "features": []}
    from shapely.geometry import LineString, Point
    origin = Origin(farm.centroid_lat, farm.centroid_lng)
    to_ll = lambda ring: [list(reversed(origin.to_latlng(x, y))) for x, y in ring]  # noqa: E731  -> [lng, lat]
    feats = []
    secs = (await s.execute(select(FieldSection).where(FieldSection.farm_id == farm_id))).scalars().all()
    for sec in secs:
        feats.append({"type": "Feature", "id": sec.id, "geometry": {"type": "Polygon", "coordinates": [to_ll(sec.polygon)]},
                      "properties": {"layer": "section", "id": sec.id, "bed": sec.bed_id, "crop": sec.crop, "state": sec.state,
                                     "weeds": sec.weeds, "moisture": sec.moisture if sec.last_survey_at else None,
                                     "health": sec.health if sec.last_survey_at else None, "surveyed": sec.last_survey_at is not None,
                                     "ndvi": sec.ndvi, "cwsi": sec.cwsi, "disease": sec.disease, "yield_est": sec.yield_est, "soil": sec.soil}})
    order = {"headland": 0, "alley": 1, "spur": 2, "lane": 3}
    for t in sorted(farm.world_config["tracks"], key=lambda t: order.get(t["kind"], 9)):
        poly = LineString(t["coords"]).buffer(t["width"] / 2, cap_style=2, join_style=2)
        rings = [poly.exterior.coords] if poly.geom_type == "Polygon" else [g.exterior.coords for g in poly.geoms]
        for i, ring in enumerate(rings):
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [to_ll(ring)]},
                          "properties": {"layer": "track", "kind": t["kind"], "id": f"{t['id']}{'' if i == 0 else i}"}})
    for hz in farm.world_config.get("hazards", []):
        ring = Point(hz["x"], hz["y"]).buffer(hz["r"], quad_segs=8).exterior.coords
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [to_ll(ring)]},
                      "properties": {"layer": "hazard", "id": hz["id"]}})
    for inf in farm.world_config["infrastructure"]:
        ring = Point(inf["x"], inf["y"]).buffer(5.0, cap_style=3).exterior.coords
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [to_ll(ring)]},
                      "properties": {"layer": "pad", "kind": inf["kind"], "id": inf["id"]}})
    return {"type": "FeatureCollection", "features": feats}


async def _robots_state(s: AsyncSession, farm: Farm) -> list[dict]:
    rows = (await s.execute(select(Robot).where(Robot.farm_id == farm.id))).scalars().all()
    return [{"id": r.id, "type": r.type, "name": r.name, "status": r.status, "active": r.active, "battery_pct": r.battery_pct,
             "x": r.x, "y": r.y, "z": r.z, "lat": r.lat, "lng": r.lng, "heading": 0.0, "speed": 0.0, "home": r.home_dock}
            for r in rows]


@router.delete("", status_code=204)
async def reset_all(s: AsyncSession = Depends(get_session)):
    """Fresh session: delete every farm (sections, infrastructure, robots cascade) and clear live state."""
    await fleet.set_active_farm_id(None)
    await fleet.set_fleet([])
    from app.services.world_engine import world as world_engine
    await world_engine.load(None)
    await s.execute(delete(Robot))
    await s.execute(delete(Farm))
    await s.commit()
    r = get_redis()
    keys = [k async for k in r.scan_iter("arcadia:*")]
    if keys:
        await r.delete(*keys)
    bridge.world = None
    bridge.farm_id = None
    await fleet.publish_dashboard({"t": "fleet_reset", "robots": []})


@router.get("/{farm_id}/world")
async def get_world(farm_id: str, s: AsyncSession = Depends(get_session)):
    farm = await get_farm_or_404(s, farm_id)
    if not farm.world_config:
        raise HTTPException(409, "farm has not been planned yet (POST /plan first)")
    return farm.world_config


@router.post("/{farm_id}/activate", response_model=FarmOut)
async def activate_farm(farm_id: str, s: AsyncSession = Depends(get_session)):
    """Make this farm the live digital twin: provision the fleet and push the world to the sim."""
    farm = await get_farm_or_404(s, farm_id)
    if not farm.world_config:
        raise HTTPException(409, "plan the farm before activating it")
    prev = await fleet.get_active_farm_id()
    if prev and prev != farm_id:
        p = (await s.execute(select(Farm).where(Farm.id == prev))).scalar_one_or_none()
        if p:
            p.status = "planned"
    # (Re)provision robots from the world config. Robot ids are fleet-wide (uav-1, ugv-1, agv-1):
    # the physical fleet moves to whichever farm is live, so drop any rows that still bind these
    # ids to another farm, and in-flight work there, before re-creating them here.
    ids = [r["id"] for r in farm.world_config["robots"]]
    await s.execute(update(Mission).where(Mission.robot_id.in_(ids), Mission.status.in_(("queued", "dispatched", "running")))
                    .values(status="cancelled", note="fleet moved to another farm"))
    await s.execute(update(Incident).where(Incident.robot_id.in_(ids), Incident.state.in_(("open", "acknowledged", "recovering")))
                    .values(state="resolved"))
    await s.execute(delete(Robot).where(or_(Robot.farm_id == farm_id, Robot.id.in_(ids))))
    robots_state = []
    for r in farm.world_config["robots"]:
        lat, lng = bridge_origin_latlng(farm, r["x"], r["y"])
        s.add(Robot(id=r["id"], farm_id=farm_id, type=r["type"], name=r["name"], status="idle" if r["active"] else "offline",
                    active=r["active"], battery_pct=100.0, x=r["x"], y=r["y"], z=r["z"], lat=lat, lng=lng, home_dock=r["home"]))
        robots_state.append({"id": r["id"], "type": r["type"], "name": r["name"], "status": "idle" if r["active"] else "offline",
                             "active": r["active"], "battery_pct": 100.0, "x": r["x"], "y": r["y"], "z": r["z"], "lat": lat,
                             "lng": lng, "heading": 0.0, "speed": 0.0, "home": r["home"], "spec": r["spec"]})
    if not farm.crop_plan:  # default: the recommended (or default) crop, sown at its next window
        from app.agro.crops import DEFAULT_CROP
        rec = next((c["crop"] for c in (farm.setup or {}).get("crop_choice", []) if c.get("recommended")), DEFAULT_CROP)
        farm.crop_plan = {"crop": rec, "rotation_crop": None, "sections": {sec["id"]: rec for sec in farm.world_config["sections"]}}
    farm.status = "active"
    await s.commit()
    await s.refresh(farm)
    await bridge.activate_farm(farm, robots_state)
    from app.services.world_engine import world as world_engine
    await world_engine.load(farm.id)
    return farm


def bridge_origin_latlng(farm: Farm, x: float, y: float) -> tuple[float, float]:
    lat, lng = Origin(farm.centroid_lat, farm.centroid_lng).to_latlng(x, y)
    return round(lat, 7), round(lng, 7)


@router.get("/{farm_id}/stats")
async def farm_stats(farm_id: str, s: AsyncSession = Depends(get_session)):
    await get_farm_or_404(s, farm_id)
    rows = (await s.execute(select(FieldSection.state, func.count()).where(FieldSection.farm_id == farm_id)
                            .group_by(FieldSection.state))).all()
    weeds = (await s.execute(select(func.count()).where(FieldSection.farm_id == farm_id, FieldSection.weeds.is_(True)))).scalar_one()
    surveyed = (await s.execute(select(func.count()).where(FieldSection.farm_id == farm_id,
                                                           FieldSection.last_survey_at.is_not(None)))).scalar_one()
    moisture = (await s.execute(select(func.avg(FieldSection.moisture)).where(FieldSection.farm_id == farm_id))).scalar_one()
    return {"states": {k: v for k, v in rows}, "weeds": weeds, "surveyed": surveyed, "avg_moisture": round(float(moisture or 0), 3),
            "unit": "sections"}
