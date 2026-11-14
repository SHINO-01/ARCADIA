"""Builds the JSON world-configuration payload ingested by the Webots C++ Supervisor spawner.

Schema version 1. Everything is expressed in local ENU metres relative to the farm origin so
the simulator never has to know about geodesy. Lat/lng are included for convenience.
"""
from __future__ import annotations

import math

from app.core.config import settings
from app.services.h3_engine import CROP_HEIGHT_M, HexGrid
from app.services.planner import PlanReport

ROBOT_CATALOG = {
    "scout_uav": {"name": "Scout UAV", "role": "aerial_survey", "max_speed": 6.0, "battery_wh": 520,
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


def build_world_config(farm_id: str, farm_name: str, grid: HexGrid, plan: PlanReport, seed: int = 42) -> dict:
    xs = [c.x for c in grid.cells]
    ys = [c.y for c in grid.cells]
    margin = grid.edge_m * 3
    minx, maxx, miny, maxy = min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin

    infra_by_h3 = {d.h3: d for d in plan.docks + plan.storage}
    cells = []
    for c in grid.cells:
        site = infra_by_h3.get(c.h3)
        cells.append({
            "h3": c.h3, "x": c.x, "y": c.y, "lat": round(c.lat, 7), "lng": round(c.lng, 7),
            "kind": "dock" if site and site.kind.endswith("dock") else "storage" if site else "crop",
            "crop": None if site else c.crop_type,
            "crop_height": 0.0 if site else CROP_HEIGHT_M[c.crop_type],
            "state": "infra" if site else c.state, "moisture": c.soil_moisture,
        })

    infrastructure = [{
        "id": s.slug, "kind": s.kind, "h3": s.h3, "x": s.x, "y": s.y, "lat": round(s.lat, 7),
        "lng": round(s.lng, 7), "capacity_kw": s.capacity_kw, "storage_kwh": s.storage_kwh,
        "cluster_size": s.cluster_size,
    } for s in plan.docks + plan.storage]

    # Spawn robots around dock-1 / storage-1, spaced along a ring so they never overlap.
    dock = plan.docks[0]
    store = plan.storage[0]
    robots = []
    for i, r in enumerate(default_roster()):
        cat = ROBOT_CATALOG[r["type"]]
        ang = i * (2 * math.pi / 5)
        base = store if r["type"] == "logistics_agv" else dock
        robots.append({
            "id": r["id"], "type": r["type"], "name": f"{cat['name']} {r['id'].split('-')[1]}",
            "active": r["active"], "home": base.slug,
            "x": round(base.x + 2.2 * math.cos(ang), 3), "y": round(base.y + 2.2 * math.sin(ang), 3),
            "z": 0.3 if r["type"] == "scout_uav" else 0.0, "spec": cat,
        })

    return {
        "schema": 1, "farm": {"id": farm_id, "name": farm_name, "seed": seed,
                              "origin": {"lat": grid.origin.lat, "lng": grid.origin.lng},
                              "area_m2": round(grid.area_m2, 1)},
        "terrain": {"min_x": round(minx, 2), "max_x": round(maxx, 2), "min_y": round(miny, 2),
                    "max_y": round(maxy, 2), "width": round(maxx - minx, 2), "height": round(maxy - miny, 2)},
        "hex": {"resolution": grid.resolution, "edge_m": round(grid.edge_m, 3), "count": len(cells),
                "cells": cells},
        "infrastructure": infrastructure,
        "robots": robots,
        "limits": {"max_active_robots": settings.max_active_robots},
    }
