"""Shared fixtures: a planned demo field as the dispatcher sees it (no database needed)."""
from __future__ import annotations

from functools import lru_cache
from types import SimpleNamespace

from shapely.geometry import Polygon
from shapely.ops import unary_union

from app.services.field import field_from_geojson
from app.services.planner import plan_infrastructure
from app.services.world_builder import build_world_config

RECT = {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[
    [-119.80, 36.70], [-119.797, 36.70], [-119.797, 36.7022], [-119.80, 36.7022], [-119.80, 36.70]]]}}


@lru_cache(maxsize=1)
def demo():
    fld = field_from_geojson(RECT)
    plan = plan_infrastructure(fld)
    world = build_world_config("demo", "Demo", fld, plan)
    farm = SimpleNamespace(id="demo", world_config=world, centroid_lat=fld.origin.lat, centroid_lng=fld.origin.lng)
    secs = {s.id: SimpleNamespace(id=s.id, bed_id=s.bed_id, crop=s.crop, x=s.x, y=s.y, angle=s.angle, length=s.length,
                                  width=s.width, passes=s.passes, polygon=s.polygon) for s in plan.layout.sections}
    polys = {sid: Polygon(s.polygon) for sid, s in secs.items()}
    crop = unary_union(list(polys.values()))
    return SimpleNamespace(field=fld, plan=plan, world=world, farm=farm, secs=secs, polys=polys, crop=crop)
