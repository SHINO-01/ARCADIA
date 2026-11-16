"""Builds the JSON world-configuration payload ingested by the Webots C++ Supervisor spawner.

Schema version 4. Everything is expressed in local ENU metres relative to the farm origin so
the simulator never has to know about geodesy. Lat/lng are included for convenience.
"""
from __future__ import annotations

import math
import random
import time

from app.core.config import settings
from app.services.field import CROP_HEIGHT_M, Field
from app.services.planner import PlanReport
from app.services.track_network import TrackNetwork

ROBOT_CATALOG = {
    "scout_uav": {"name": "Scout UAV", "role": "aerial_survey", "max_speed": 6.0, "battery_wh": 900,
                  "cruise_alt": 6.0, "sensor_radius_m": 9.0, "mass_kg": 2.4, "solar_skin_w": 12},
    "seeder_ugv": {"name": "Seeder UGV", "role": "seeding", "max_speed": 1.4, "battery_wh": 2400,
                   "cruise_alt": 0.0, "sensor_radius_m": 2.0, "mass_kg": 180, "solar_skin_w": 0},
    "cropcare_ugv": {"name": "Crop Care UGV", "role": "weeding", "max_speed": 1.8, "battery_wh": 1200,
                     "cruise_alt": 0.0, "sensor_radius_m": 1.5, "mass_kg": 95, "solar_skin_w": 40},
    "harvester_ugv": {"name": "Harvester UGV", "role": "harvesting", "max_speed": 1.0, "battery_wh": 3200,
                      "cruise_alt": 0.0, "sensor_radius_m": 1.2, "mass_kg": 260, "solar_skin_w": 0},
    "logistics_agv": {"name": "Logistics AGV", "role": "transport_tow", "max_speed": 1.6, "battery_wh": 6000,
                      "cruise_alt": 0.0, "sensor_radius_m": 2.5, "mass_kg": 420, "solar_skin_w": 0},
}

ROBOT_PREFIX = {"scout_uav": "uav", "seeder_ugv": "ugv", "cropcare_ugv": "ugv", "harvester_ugv": "ugv",
                "logistics_agv": "agv"}


def default_roster() -> list[dict]:
    roster = []
    counters: dict[str, int] = {}
    for t in settings.robot_roster:
        if t not in ROBOT_CATALOG:
            continue
        p = ROBOT_PREFIX[t]
        counters[p] = counters.get(p, 0) + 1
        roster.append({"id": f"{p}-{counters[p]}", "type": t, "active": True})
    # The Logistics AGV is always part of the fleet but stays powered-down in storage until a
    # tow is dispatched, so the active count on the IdeaPad never exceeds max_active_robots.
    if not any(r["type"] == "logistics_agv" for r in roster):
        roster.append({"id": "agv-1", "type": "logistics_agv", "active": False})
    return roster


def build_world_config(farm_id: str, farm_name: str, fld: Field, plan: PlanReport, seed: int = 42) -> dict:
    """World config schema 4: a controlled-traffic field (bed sections + tracks) for the simulators,
    plus the noded track graph so robot controllers can route themselves along the tracks."""
    L = plan.layout
    rng = random.Random(seed)
    xs = [pt[0] for t in L.tracks for pt in t.coords] + [s.x for s in plan.sites]
    ys = [pt[1] for t in L.tracks for pt in t.coords] + [s.y for s in plan.sites]
    margin = 25.0
    minx, maxx, miny, maxy = min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin

    sections = []
    for sec in L.sections:
        lat, lng = fld.origin.to_latlng(sec.x, sec.y)
        sections.append({
            "id": sec.id, "bed": sec.bed_id, "crop": sec.crop, "x": sec.x, "y": sec.y, "lat": round(lat, 7),
            "lng": round(lng, 7), "length": round(sec.length, 3), "width": sec.width, "angle": round(sec.angle, 6),
            "crop_height": CROP_HEIGHT_M[sec.crop], "state": "fallow",
            "moisture": round(min(1.0, max(0.1, rng.gauss(0.52, 0.12))), 3), "passes": sec.passes,
        })
    tracks = [{"id": t.id, "kind": t.kind, "width": t.width, "coords": t.coords} for t in L.tracks]
    infrastructure = [{
        "id": s.slug, "kind": s.kind, "x": s.x, "y": s.y, "lat": round(s.lat, 7), "lng": round(s.lng, 7),
        "capacity_kw": s.capacity_kw, "storage_kwh": s.storage_kwh, "cluster_size": s.cluster_size, "access": s.access,
    } for s in plan.sites]

    dock = plan.docks[0]
    store = plan.storage[0]
    robots = []
    for i, r in enumerate(default_roster()):
        cat = ROBOT_CATALOG[r["type"]]
        base = store if r["type"] == "logistics_agv" else dock
        # park on the yard pad, spaced so units never overlap
        ang = i * (2 * math.pi / 5)
        robots.append({
            "id": r["id"], "type": r["type"], "name": f"{cat['name']} {r['id'].split('-')[1]}",
            "active": r["active"], "home": base.slug, "repair": plan.repair[0].slug if plan.repair else None,
            "x": round(base.x + 2.2 * math.cos(ang), 3), "y": round(base.y + 2.2 * math.sin(ang), 3),
            "z": 0.3 if r["type"] == "scout_uav" else 0.0, "spec": cat,
        })

    return {
        "schema": 4,
        "farm": {"id": farm_id, "name": farm_name, "seed": seed, "revision": round(time.time(), 3),
                 "origin": {"lat": fld.origin.lat, "lng": fld.origin.lng}, "area_m2": round(fld.area_m2, 1)},
        "terrain": {"min_x": round(minx, 2), "max_x": round(maxx, 2), "min_y": round(miny, 2), "max_y": round(maxy, 2),
                    "width": round(maxx - minx, 2), "height": round(maxy - miny, 2),
                    "boundary": [[round(x, 3), round(y, 3)] for x, y in
                                 (fld.origin.to_xy(lat, lng) for lng, lat in fld.boundary_lnglat)]},
        "layout": {"angle": round(L.angle, 6), "bed_width": L.params.bed_width, "lane_width": L.params.lane_width,
                   "headland_width": L.params.headland_width, "beds": L.beds},
        "sections": sections, "tracks": tracks, "hazards": L.hazards,
        "track_graph": TrackNetwork(tracks).graph(),
        "infrastructure": infrastructure, "robots": robots,
        "limits": {"max_active_robots": settings.max_active_robots},
    }
