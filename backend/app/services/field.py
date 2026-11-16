"""Farm field geometry and crop constants.

A farm is its boundary polygon. Everything downstream works in a local ENU (east-north-up)
tangent plane centred on the field centroid, in metres: the controlled-traffic layout, the track
network, the simulators. Lat/lng only appear at the API edge.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Polygon

from app.services.geo import Origin, polygon_area_m2, polygon_from_geojson

CROP_TYPES = ["wheat", "maize", "soy", "tomato", "lettuce"]
CROP_HEIGHT_M = {"wheat": 0.9, "maize": 1.8, "soy": 0.6, "tomato": 1.1, "lettuce": 0.3}
# Yield per square metre of bed (kg)
YIELD_KG_M2 = {"wheat": 0.7, "maize": 1.1, "soy": 0.35, "tomato": 4.5, "lettuce": 2.2}

MAX_FIELD_AREA_M2 = 2_000_000.0  # 200 ha: beyond this the local sim gets too heavy


@dataclass
class Field:
    origin: Origin
    area_m2: float
    boundary_lnglat: list[tuple[float, float]] = field(default_factory=list)

    @property
    def polygon_xy(self) -> Polygon:
        """The boundary in local ENU metres."""
        return Polygon([self.origin.to_xy(lat, lng) for lng, lat in self.boundary_lnglat])


def field_from_geojson(boundary: dict) -> Field:
    poly = polygon_from_geojson(boundary)
    c = poly.centroid
    return Field(origin=Origin(lat=c.y, lng=c.x), area_m2=polygon_area_m2(poly),
                 boundary_lnglat=[(x, y) for x, y in poly.exterior.coords])


def field_of_farm(farm) -> Field:
    """Rebuild the Field of a stored farm (keeps the origin the farm was created with)."""
    poly = polygon_from_geojson(farm.boundary)
    return Field(origin=Origin(farm.centroid_lat, farm.centroid_lng), area_m2=farm.area_m2,
                 boundary_lnglat=[(x, y) for x, y in poly.exterior.coords])
