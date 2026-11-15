"""Layer 1 - The Ground: farms, hex grids, infrastructure planning, world config, activation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_farm_or_404
from app.bridge.manager import bridge
from app.core.db import get_session
from app.core.redis import get_redis
from app.models import Farm, HexCell, Incident, Infrastructure, Mission, Robot
from app.schemas import FarmCreate, FarmOut, InfrastructureOut, PlanRequest
from app.services import fleet
from app.services.h3_engine import HexGrid, build_hexgrid, hexgrid_feature_collection
from app.services.planner import plan_infrastructure
from app.services.world_builder import build_world_config

router = APIRouter(prefix="/farms", tags=["farms"])


@router.get("", response_model=list[FarmOut])
async def list_farms(s: AsyncSession = Depends(get_session)):
    return (await s.execute(select(Farm).order_by(Farm.created_at.desc()))).scalars().all()


@router.post("", response_model=FarmOut, status_code=201)
async def create_farm(body: FarmCreate, s: AsyncSession = Depends(get_session)):
    try:
        grid = build_hexgrid(body.boundary, body.resolution, body.seed, body.target_cells)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if len(grid.cells) > 6000:
        raise HTTPException(422, f"{len(grid.cells)} cells is too many for the local sim; pick a smaller area or lower resolution")
    farm = Farm(name=body.name, boundary=body.boundary, centroid_lat=grid.origin.lat, centroid_lng=grid.origin.lng,
                area_m2=round(grid.area_m2, 1), h3_resolution=grid.resolution, cell_count=len(grid.cells), seed=body.seed)
    s.add(farm)
    await s.flush()
    s.add_all([HexCell(farm_id=farm.id, h3_index=c.h3, lat=c.lat, lng=c.lng, x=c.x, y=c.y, kind=c.kind,
                       crop_type=c.crop_type, state=c.state, soil_moisture=c.soil_moisture) for c in grid.cells])
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
    await s.execute(delete(Robot).where(Robot.farm_id == farm_id))
    await s.delete(farm)
    await s.commit()


@router.get("/{farm_id}/hexgrid")
async def get_hexgrid(farm_id: str, s: AsyncSession = Depends(get_session)):
    await get_farm_or_404(s, farm_id)
    cells = (await s.execute(select(HexCell).where(HexCell.farm_id == farm_id))).scalars().all()
    return hexgrid_feature_collection(cells, extra=lambda c: {
        "last_survey_at": c.last_survey_at.isoformat() if c.last_survey_at else None})


@router.get("/{farm_id}/infrastructure", response_model=list[InfrastructureOut])
async def get_infrastructure(farm_id: str, s: AsyncSession = Depends(get_session)):
    await get_farm_or_404(s, farm_id)
    return (await s.execute(select(Infrastructure).where(Infrastructure.farm_id == farm_id))).scalars().all()


def _grid_from_db(farm: Farm, cells: list[HexCell]) -> HexGrid:
    from app.services.geo import Origin
    from app.services.h3_engine import HexCellSpec
    import h3 as _h3
    return HexGrid(resolution=farm.h3_resolution, origin=Origin(farm.centroid_lat, farm.centroid_lng), area_m2=farm.area_m2,
                   edge_m=_h3.average_hexagon_edge_length(farm.h3_resolution, unit="m"),
                   cells=_with_neighbors([HexCellSpec(h3=c.h3_index, lat=c.lat, lng=c.lng, x=c.x, y=c.y, crop_type=c.crop_type or "wheat",
                                      kind="crop", state=c.state if c.state != "infra" else "fallow", weeds=c.weeds,
                                      soil_moisture=c.soil_moisture) for c in cells]))


def _with_neighbors(specs):
    import h3 as _h3
    known = {c.h3 for c in specs}
    for c in specs:
        c.neighbors = [n for n in _h3.grid_ring(c.h3, 1) if n in known]
    return specs


@router.post("/{farm_id}/plan", response_model=FarmOut)
async def plan_farm(farm_id: str, body: PlanRequest, s: AsyncSession = Depends(get_session)):
    """Sprint 2: K-Means docks + storage/repair, travel roads, crop beds, world-config payload.
    Re-running with other parameters repositions roads and beds."""
    farm = await get_farm_or_404(s, farm_id)
    cells = (await s.execute(select(HexCell).where(HexCell.farm_id == farm_id))).scalars().all()
    grid = _grid_from_db(farm, cells)
    seed = body.seed if body.seed is not None else farm.seed
    report = plan_infrastructure(grid, body.k_docks, body.k_storage, seed, road_spacing=body.road_spacing,
                                 bed_width=body.bed_width, orientation=body.orientation, shuffle_crops=body.shuffle_crops)
    await s.execute(delete(Infrastructure).where(Infrastructure.farm_id == farm_id))
    spec_by_h3 = {c.h3: c for c in grid.cells}
    bed_of = {h: b.id for b in report.beds for h in b.cells}
    for c in cells:
        spec = spec_by_h3[c.h3_index]
        c.kind = "crop"
        c.bed_id = bed_of.get(c.h3_index)
        c.crop_type = spec.crop_type
        if spec.kind == "road":
            c.kind, c.state, c.crop_type, c.bed_id = "road", "infra", None, None
        elif c.state == "infra":
            c.state = "fallow"
    by_h3 = {c.h3_index: c for c in cells}
    for site in report.sites:
        s.add(Infrastructure(farm_id=farm_id, slug=site.slug, kind=site.kind, h3_index=site.h3, lat=site.lat, lng=site.lng,
                             x=site.x, y=site.y, cluster_size=site.cluster_size, capacity_kw=site.capacity_kw,
                             storage_kwh=site.storage_kwh, stored_kwh=round(site.storage_kwh * 0.65, 1)))
        cell = by_h3.get(site.h3)
        if cell is not None:
            cell.kind = "dock" if site.kind.endswith("dock") else "repair" if site.kind == "repair_station" else "storage"
            cell.state, cell.crop_type, cell.bed_id = "infra", None, None
    farm.plan = report.to_dict()
    farm.world_config = build_world_config(farm.id, farm.name, grid, report, seed)
    farm.status = "planned" if farm.status != "active" else "active"
    await s.commit()
    await s.refresh(farm)
    if farm.status == "active":  # re-planning a live farm: push the new world to the sim
        await bridge.activate_farm(farm, await _robots_state(s, farm))
    return farm


async def _robots_state(s: AsyncSession, farm: Farm) -> list[dict]:
    rows = (await s.execute(select(Robot).where(Robot.farm_id == farm.id))).scalars().all()
    return [{"id": r.id, "type": r.type, "name": r.name, "status": r.status, "active": r.active, "battery_pct": r.battery_pct,
             "x": r.x, "y": r.y, "z": r.z, "lat": r.lat, "lng": r.lng, "heading": 0.0, "speed": 0.0, "home": r.home_dock}
            for r in rows]


@router.delete("", status_code=204)
async def reset_all(s: AsyncSession = Depends(get_session)):
    """Fresh session: delete every farm (cells, infrastructure, robots cascade) and clear live state."""
    await fleet.set_active_farm_id(None)
    await fleet.set_fleet([])
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
    farm.status = "active"
    await s.commit()
    await s.refresh(farm)
    await bridge.activate_farm(farm, robots_state)
    return farm


def bridge_origin_latlng(farm: Farm, x: float, y: float) -> tuple[float, float]:
    from app.services.geo import Origin
    lat, lng = Origin(farm.centroid_lat, farm.centroid_lng).to_latlng(x, y)
    return round(lat, 7), round(lng, 7)


@router.get("/{farm_id}/stats")
async def farm_stats(farm_id: str, s: AsyncSession = Depends(get_session)):
    await get_farm_or_404(s, farm_id)
    rows = (await s.execute(select(HexCell.state, func.count()).where(HexCell.farm_id == farm_id, HexCell.kind == "crop")
                            .group_by(HexCell.state))).all()
    weeds = (await s.execute(select(func.count()).where(HexCell.farm_id == farm_id, HexCell.weeds.is_(True)))).scalar_one()
    surveyed = (await s.execute(select(func.count()).where(HexCell.farm_id == farm_id, HexCell.last_survey_at.is_not(None)))).scalar_one()
    moisture = (await s.execute(select(func.avg(HexCell.soil_moisture)).where(HexCell.farm_id == farm_id, HexCell.kind == "crop"))).scalar_one()
    return {"states": {k: v for k, v in rows}, "weeds": weeds, "surveyed": surveyed, "avg_moisture": round(float(moisture or 0), 3)}
