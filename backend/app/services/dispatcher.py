"""Automated Dispatch on a controlled-traffic field.

Runs from a Celery beat tick. Agronomic state lives on bed *sections*:
  * the Scout UAV surveys sections whose last observation is stale (lawn-mower order, bed by bed);
  * ground units take the season's field work (seed, fertilise, weed, spray, harvest) from the
    world model's campaign queues, shared with the farm's virtual fleet; they work a section by
    driving the tramline beside it ("work pass"); they reach it,
    and every other destination, over the track network only - never across a bed;
  * harvest is the one exception: the harvester leaves the tramline at the section's end, drives
    down the bed's centre line cutting the crop, and steps back onto the same tramline.
Operator overrides (POST /fleet/{id}/dispatch|route) bypass this queue.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Farm, FieldSection, Infrastructure, Mission, Robot
from app.services.track_network import TrackNetwork

log = logging.getLogger("arcadia.dispatch")

SURVEY_STALE_S = 300.0
SURVEY_BATCH = 24
MIN_BATTERY = 30.0
UGV_TYPES = ("seeder_ugv", "cropcare_ugv", "harvester_ugv")
UAV_ALT = 6.0
PLANTED = ("seeded", "growing", "ripe")

# Battery percent consumed per metre at cruise (compressed sim time), used for range checks.
PCT_PER_M = {"scout_uav": 0.04, "logistics_agv": 0.02}
DEFAULT_PCT_PER_M = 0.045

_net_cache: dict[tuple[str, float], TrackNetwork] = {}


def track_network(farm: Farm) -> TrackNetwork | None:
    """Cached per farm world revision."""
    w = farm.world_config or {}
    if w.get("schema", 0) < 3:
        return None
    key = (farm.id, float(w.get("farm", {}).get("revision", 0.0)))
    net = _net_cache.get(key)
    if net is None:
        for k in [k for k in _net_cache if k[0] == farm.id]:
            del _net_cache[k]
        net = _net_cache[key] = TrackNetwork(w["tracks"])
    return net


def _wp(origin_lat: float, origin_lng: float, x: float, y: float, z: float = 0.0) -> dict:
    from app.services.geo import Origin
    lat, lng = Origin(origin_lat, origin_lng).to_latlng(x, y)
    return {"x": round(x, 3), "y": round(y, 3), "z": z, "lat": round(lat, 7), "lng": round(lng, 7)}


def ground_route(farm: Farm, sx: float, sy: float, tx: float, ty: float) -> tuple[list[dict], float]:
    """On-track waypoints (turns only) from (sx,sy) to (tx,ty); the goal is snapped onto a track."""
    net = track_network(farm)
    if net is None:
        return [_wp(farm.centroid_lat, farm.centroid_lng, tx, ty)], math.dist((sx, sy), (tx, ty))
    pts, length = net.route(sx, sy, tx, ty)
    if not pts:
        return [], float("inf")
    pts = pts[1:] if len(pts) > 1 and math.dist(pts[0], (sx, sy)) < 0.6 else pts
    return [_wp(farm.centroid_lat, farm.centroid_lng, x, y) for x, y in pts], length


HARVEST_INSET_M = 0.6  # the harvester turns onto the bed just inside the section's end


def _bed_centre(sec: FieldSection, p: list[float], inset: float) -> tuple[float, float]:
    """Point on the section's centre line level with tramline point `p`, pulled `inset` metres
    towards the section's middle."""
    dx, dy = math.cos(sec.angle), math.sin(sec.angle)
    u = (p[0] - sec.x) * dx + (p[1] - sec.y) * dy
    u = max(-sec.length / 2 + inset, min(sec.length / 2 - inset, u))
    return sec.x + u * dx, sec.y + u * dy


def section_mission(farm: Farm, sec: FieldSection, sx: float, sy: float, kind: str = "weed") -> tuple[list[dict], int, float]:
    """Route over the tracks to the nearer end of the nearer tramline beside `sec`, then the work pass.
    seed/weed: the pass runs along the tramline, the tool reaching into the bed.
    harvest:   the pass turns onto the bed, runs down its centre line and returns to the tramline.
    Returns (waypoints, index where the pass starts, total length)."""
    best = None
    for ps in sec.passes or []:
        for a, b in ((ps["start"], ps["end"]), (ps["end"], ps["start"])):
            wps, length = ground_route(farm, sx, sy, a[0], a[1])
            if wps and (best is None or length < best[3]):
                best = (wps, a, b, length)
    if best is None:
        return [], 0, float("inf")
    wps, start, end, length = best
    pass_from = len(wps) - 1
    o = (farm.centroid_lat, farm.centroid_lng)
    if kind == "harvest":
        c0, c1 = _bed_centre(sec, start, HARVEST_INSET_M), _bed_centre(sec, end, HARVEST_INSET_M)
        wps += [_wp(*o, *c0), _wp(*o, *c1), _wp(*o, end[0], end[1])]
        length += math.dist(start, c0) + math.dist(c0, c1) + math.dist(c1, end)
    else:
        wps.append(_wp(*o, end[0], end[1]))
        length += sec.length
    return wps, pass_from, length


def nearest_dock(s: Session, farm: Farm, robot_type: str, x: float, y: float) -> tuple[Infrastructure, list[dict], float] | None:
    """Charging dock with the shortest *travel* distance (tracks for ground units, straight for UAVs)."""
    docks = s.execute(select(Infrastructure).where(Infrastructure.farm_id == farm.id, Infrastructure.kind.like("%dock"))).scalars().all()
    best = None
    for d in docks:
        if robot_type == "scout_uav":
            wps, length = [_wp(farm.centroid_lat, farm.centroid_lng, d.x, d.y, 0.0)], math.dist((x, y), (d.x, d.y))
        else:
            wps, length = ground_route(farm, x, y, d.x, d.y)
        if wps and (best is None or length < best[2]):
            best = (d, wps, length)
    return best


def _lawnmower(secs: list[FieldSection], start_xy: tuple[float, float]) -> list[FieldSection]:
    """Bed-by-bed boustrophedon: alternate direction per bed so the UAV sweeps in long rows."""
    by_bed: dict[str, list[FieldSection]] = {}
    for s in secs:
        by_bed.setdefault(s.bed_id, []).append(s)
    beds = sorted(by_bed, key=lambda b: (len(b), b))
    out: list[FieldSection] = []
    for i, b in enumerate(beds):
        row = sorted(by_bed[b], key=lambda s: s.id, reverse=bool(i % 2))
        out.extend(row)
    return out


def plan_for_robot(s: Session, farm: Farm, robot: Robot, state: dict) -> Mission | None:
    """Return a new (unsaved) Mission for an idle robot, or None if nothing to do."""
    battery = float(state.get("battery_pct", robot.battery_pct or 100.0))
    rx, ry = float(state.get("x", robot.x or 0.0)), float(state.get("y", robot.y or 0.0))
    dock = nearest_dock(s, farm, robot.type, rx, ry)
    if dock is not None:
        d, wps, length = dock
        needed = length * PCT_PER_M.get(robot.type, DEFAULT_PCT_PER_M)
        if battery < MIN_BATTERY or battery < needed * 1.5 + 12.0:
            return Mission(farm_id=farm.id, robot_id=robot.id, kind="charge", source="auto", priority=0, waypoints=wps,
                           payload={"dock": d.slug, "distance_m": round(length)},
                           note=f"Battery {battery:.0f}% · nearest dock {d.slug} ({length:.0f} m by track)")

    secs = s.execute(select(FieldSection).where(FieldSection.farm_id == farm.id)).scalars().all()
    if not secs:
        return None
    now = datetime.now(timezone.utc)

    if robot.type == "scout_uav":
        stale = [c for c in secs if c.last_survey_at is None or (now - c.last_survey_at) > timedelta(seconds=SURVEY_STALE_S)]
        if not stale:
            return None
        oldest = min(stale, key=lambda c: (c.last_survey_at or datetime.min.replace(tzinfo=timezone.utc), c.id))
        stale.sort(key=lambda c: (c.x - oldest.x) ** 2 + (c.y - oldest.y) ** 2)
        batch = _lawnmower(stale[:SURVEY_BATCH], (rx, ry))
        wps = [{**_wp(farm.centroid_lat, farm.centroid_lng, c.x, c.y, UAV_ALT), "section": c.id} for c in batch]
        return Mission(farm_id=farm.id, robot_id=robot.id, kind="survey", source="auto", priority=3, waypoints=wps,
                       payload={"sections": [c.id for c in batch]}, note=f"Aerial survey of {len(batch)} bed sections")

    if robot.type in UGV_TYPES:
        # Field work comes from the season's campaigns (world model): the virtual fleet works the same
        # queues; a claim keeps it off the section this robot is heading for.
        import json as _json
        import time as _time

        from app.core.redis import get_sync_redis
        from app.services.operations import KINDS
        r = get_sync_redis()
        raw = r.get("arcadia:ops:pending")
        if not raw:
            return None
        ops = _json.loads(raw)
        if ops.get("farm_id") != farm.id or not ops.get("workable", True):
            return None
        claims = {k.decode() if isinstance(k, bytes) else k for k in r.hkeys("arcadia:ops:claims")}
        by_id = {c.id: c for c in secs}
        for task in KINDS:
            if task == "harvest" and not ops.get("harvest_ok", True):
                continue
            cand = [by_id[sid] for sid in ops["pending"].get(task, []) if sid in by_id and sid not in claims]
            if not cand:
                continue
            cand.sort(key=lambda c: (c.x - rx) ** 2 + (c.y - ry) ** 2)
            best = None
            for c in cand[:6]:
                wps, pass_from, length = section_mission(farm, c, rx, ry, task)
                if wps and (best is None or length < best[3]):
                    best = (c, wps, pass_from, length)
            if best is None:
                continue
            c, wps, pass_from, length = best
            prio = {"harvest": 1, "spray": 2, "weed": 2, "fertilise": 3, "seed": 4}[task]
            r.hset("arcadia:ops:claims", c.id, _json.dumps({"kind": task, "robot": robot.id, "ts": _time.time()}))
            return Mission(farm_id=farm.id, robot_id=robot.id, kind=task, source="auto", priority=prio, target_section=c.id,
                           waypoints=wps, payload={"section": c.id, "bed": c.bed_id, "crop": c.crop, "pass_from": pass_from},
                           note=f"{task.title()} section {c.id} ({c.crop}) · " +
                                (f"bed pass {c.length:.0f} m" if task == "harvest" else f"tramline pass {c.length:.0f} m"))
    return None
