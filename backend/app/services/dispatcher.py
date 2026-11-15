"""Automated Dispatch: turns known field state + fleet availability into missions.

Runs from a Celery beat tick. Operator overrides (POST /fleet/{id}/dispatch) bypass this
queue and go straight to the robot via the bridge, pausing automation for that robot.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Farm, HexCell, Infrastructure, Mission, Robot
from app.services.missions import boustrophedon, direct
from app.services.routing import RoadNetwork

log = logging.getLogger("arcadia.dispatch")

SURVEY_STALE_S = 240.0
SURVEY_BATCH = 36
MIN_BATTERY = 30.0
UGV_TYPES = ("seeder_ugv", "cropcare_ugv", "harvester_ugv")
# Under the 2-robot cap the single active UGV is multi-tool: it may seed, weed and harvest.
UGV_MULTITOOL = True
ROLE_TASK = {"seeder_ugv": "seed", "cropcare_ugv": "weed", "harvester_ugv": "harvest"}


def _cell_dict(c: HexCell) -> dict:
    return {"h3": c.h3_index, "x": c.x, "y": c.y, "lat": c.lat, "lng": c.lng}


def road_network(s: Session, farm_id: str) -> RoadNetwork:
    rows = s.execute(select(HexCell.h3_index, HexCell.x, HexCell.y, HexCell.lat, HexCell.lng, HexCell.kind)
                     .where(HexCell.farm_id == farm_id)).all()
    return RoadNetwork([{"h3": r[0], "x": r[1], "y": r[2], "lat": r[3], "lng": r[4], "kind": r[5]} for r in rows])


def ground_route(s: Session, farm_id: str, sx: float, sy: float, target: dict) -> list[dict]:
    """Road-following waypoints for UGV/AGV missions."""
    return [w.model_dump() for w in road_network(s, farm_id).route(sx, sy, target)]


# Battery percent consumed per metre at cruise (compressed sim time), used for range checks.
PCT_PER_M = {"scout_uav": 0.04, "logistics_agv": 0.02}  # calibrated from the drain models (+~20%)
DEFAULT_PCT_PER_M = 0.045


def _path_len(x: float, y: float, wps: list[dict]) -> float:
    pts = [(x, y)] + [(w["x"], w["y"]) for w in wps]
    return sum(((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5 for i in range(len(pts) - 1))


def nearest_dock(s: Session, farm_id: str, robot_type: str, x: float, y: float) -> tuple[Infrastructure, list[dict], float] | None:
    """Charging dock with the shortest *travel* distance (roads for ground units, straight for UAVs)."""
    docks = s.execute(select(Infrastructure).where(Infrastructure.farm_id == farm_id, Infrastructure.kind.like("%dock"))).scalars().all()
    if not docks:
        return None
    net = road_network(s, farm_id) if robot_type != "scout_uav" else None
    best = None
    for d in docks:
        tgt = {"x": d.x, "y": d.y, "h3": d.h3_index, "lat": d.lat, "lng": d.lng}
        wps = [direct(tgt)[0].model_dump()] if net is None else [w.model_dump() for w in net.route(x, y, tgt)]
        length = _path_len(x, y, wps)
        if best is None or length < best[2]:
            best = (d, wps, length)
    return best


def plan_for_robot(s: Session, farm: Farm, robot: Robot, state: dict) -> Mission | None:
    """Return a new (unsaved) Mission for an idle robot, or None if nothing to do."""
    battery = float(state.get("battery_pct", robot.battery_pct or 100.0))
    rx, ry = float(state.get("x", robot.x or 0.0)), float(state.get("y", robot.y or 0.0))
    dock = nearest_dock(s, farm.id, robot.type, rx, ry)
    if dock is not None:
        d, wps, length = dock
        needed = length * PCT_PER_M.get(robot.type, DEFAULT_PCT_PER_M)
        # Go charge when below the floor, or when the trip to the nearest dock would eat the reserve.
        if battery < MIN_BATTERY or battery < needed * 1.5 + 12.0:
            return Mission(farm_id=farm.id, robot_id=robot.id, kind="charge", source="auto", priority=0, waypoints=wps,
                           payload={"dock": d.slug, "distance_m": round(length)},
                           note=f"Battery {battery:.0f}% · nearest dock {d.slug} ({length:.0f} m)")

    cells = s.execute(select(HexCell).where(HexCell.farm_id == farm.id, HexCell.kind == "crop")).scalars().all()
    if not cells:
        return None
    now = datetime.now(timezone.utc)

    if robot.type == "scout_uav":
        stale = [c for c in cells if c.last_survey_at is None or (now - c.last_survey_at) > timedelta(seconds=SURVEY_STALE_S)]
        if not stale:
            return None
        stale.sort(key=lambda c: (c.last_survey_at or datetime.min.replace(tzinfo=timezone.utc)))
        # Pick the oldest cell and sweep a compact neighbourhood around it (fewer long transits).
        seed = stale[0]
        stale.sort(key=lambda c: (c.x - seed.x) ** 2 + (c.y - seed.y) ** 2)
        batch = [_cell_dict(c) for c in stale[:SURVEY_BATCH]]
        row_h = 1.5 * max(1.0, (farm.area_m2 / max(farm.cell_count, 1)) ** 0.5 * 0.5)
        wps = boustrophedon(batch, row_height=row_h, altitude=6.0)
        return Mission(farm_id=farm.id, robot_id=robot.id, kind="survey", source="auto", priority=3,
                       waypoints=[w.model_dump() for w in wps], payload={"cells": [c["h3"] for c in batch]},
                       note=f"Aerial survey of {len(batch)} hex cells")

    if robot.type in UGV_TYPES:
        tasks = ["harvest", "weed", "seed"] if UGV_MULTITOOL else [ROLE_TASK[robot.type]]
        for task in tasks:
            if task == "harvest":
                cand = [c for c in cells if c.state == "ripe"]
            elif task == "weed":
                cand = [c for c in cells if c.weeds]
            else:
                cand = [c for c in cells if c.state in ("fallow", "harvested") and c.last_survey_at is not None]
            if not cand:
                continue
            tgt = min(cand, key=lambda c: (c.x - rx) ** 2 + (c.y - ry) ** 2)
            prio = {"harvest": 1, "weed": 2, "seed": 4}[task]
            return Mission(farm_id=farm.id, robot_id=robot.id, kind=task, source="auto", priority=prio, target_h3=tgt.h3_index,
                           waypoints=ground_route(s, farm.id, rx, ry, _cell_dict(tgt)),
                           payload={"crop": tgt.crop_type, "bed": tgt.bed_id}, note=f"{task.title()} {tgt.bed_id or 'cell'} · {tgt.crop_type}")
    return None
