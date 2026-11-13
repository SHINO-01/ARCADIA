"""Geodesy helpers: GeoJSON polygon utilities and a local ENU (east-north-up) tangent plane.

Webots worlds use ENU with metres; the dashboard uses WGS84. Every position crossing the
bridge is converted with these two functions so both views stay in lock-step.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon, shape

M_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class Origin:
    lat: float
    lng: float

    @property
    def m_per_deg_lng(self) -> float:
        return M_PER_DEG_LAT * math.cos(math.radians(self.lat))

    def to_xy(self, lat: float, lng: float) -> tuple[float, float]:
        return ((lng - self.lng) * self.m_per_deg_lng, (lat - self.lat) * M_PER_DEG_LAT)

    def to_latlng(self, x: float, y: float) -> tuple[float, float]:
        return (self.lat + y / M_PER_DEG_LAT, self.lng + x / self.m_per_deg_lng)


def polygon_from_geojson(geojson: dict) -> Polygon:
    """Accept a Feature, FeatureCollection (first polygon) or bare Polygon/MultiPolygon geometry."""
    g = geojson
    if g.get("type") == "FeatureCollection":
        feats = [f for f in g.get("features", []) if f.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon")]
        if not feats:
            raise ValueError("FeatureCollection contains no Polygon feature")
        g = feats[0]
    if g.get("type") == "Feature":
        g = g["geometry"]
    poly = shape(g)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda p: p.area)
    if poly.geom_type != "Polygon":
        raise ValueError(f"Unsupported geometry type {poly.geom_type}")
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        raise ValueError("Boundary polygon is empty")
    return poly


def polygon_area_m2(poly: Polygon) -> float:
    """Planar area on the local tangent plane (accurate to <0.1% for farm-sized parcels)."""
    c = poly.centroid
    origin = Origin(c.y, c.x)
    xy = [origin.to_xy(lat, lng) for lng, lat in poly.exterior.coords]
    return abs(Polygon(xy).area)


def polygon_geometry(poly: Polygon) -> dict:
    return {"type": "Polygon", "coordinates": [list(map(list, poly.exterior.coords))]}


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
