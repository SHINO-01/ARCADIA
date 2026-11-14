"""Procedural hex-mapping with Uber H3.

Turns a farm boundary (GeoJSON) into a tessellated hex grid at an automatically chosen
resolution, assigns procedural crop rows, and exposes geometry helpers for the planner and
the world builder.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import h3

from app.core.config import settings
from app.services.geo import Origin, polygon_area_m2, polygon_from_geojson

CROP_TYPES = ["wheat", "maize", "soy", "tomato", "lettuce"]
CROP_HEIGHT_M = {"wheat": 0.9, "maize": 1.8, "soy": 0.6, "tomato": 1.1, "lettuce": 0.3}
CROP_YIELD_KG = {"wheat": 1.2, "maize": 2.4, "soy": 0.9, "tomato": 3.1, "lettuce": 1.5}


@dataclass
class HexCellSpec:
    h3: str
    lat: float
    lng: float
    x: float
    y: float
    crop_type: str
    kind: str = "crop"
    state: str = "fallow"
    weeds: bool = False
    soil_moisture: float = 0.5
    neighbors: list[str] = field(default_factory=list)


@dataclass
class HexGrid:
    resolution: int
    origin: Origin
    area_m2: float
    edge_m: float
    cells: list[HexCellSpec]

    @property
    def by_index(self) -> dict[str, HexCellSpec]:
        return {c.h3: c for c in self.cells}


def choose_resolution(area_m2: float, target_cells: int | None = None) -> int:
    """Pick the H3 resolution whose average hexagon count best matches target_cells."""
    target = target_cells or settings.h3_target_cells
    best_res, best_err = settings.h3_min_resolution, float("inf")
    for res in range(settings.h3_min_resolution, settings.h3_max_resolution + 1):
        n = area_m2 / h3.average_hexagon_area(res, unit="m^2")
        err = abs(n - target) / target
        if err < best_err:
            best_res, best_err = res, err
    return best_res


def build_hexgrid(boundary: dict, resolution: int | None = None, seed: int = 42,
                  target_cells: int | None = None) -> HexGrid:
    poly = polygon_from_geojson(boundary)
    area = polygon_area_m2(poly)
    res = resolution if resolution is not None else choose_resolution(area, target_cells)
    c = poly.centroid
    origin = Origin(lat=c.y, lng=c.x)

    # h3 v4 accepts GeoJSON-style (lng, lat) geometry via geo_to_cells
    geom = {"type": "Polygon", "coordinates": [list(map(list, poly.exterior.coords))]}
    cells_idx = h3.geo_to_cells(geom, res)
    if not cells_idx:
        # Tiny polygons: fall back to the single cell containing the centroid
        cells_idx = {h3.latlng_to_cell(c.y, c.x, res)}
    cells_idx = sorted(cells_idx)

    rng = random.Random(seed)
    anchor = cells_idx[0]
    # Procedural crop rows: stripes along the hex IJ lattice, with a seeded crop rotation.
    rotation = CROP_TYPES[:]
    rng.shuffle(rotation)
    stripe_width = max(2, int(round(len(cells_idx) ** 0.5 / 6)))

    specs: list[HexCellSpec] = []
    for h in cells_idx:
        lat, lng = h3.cell_to_latlng(h)
        x, y = origin.to_xy(lat, lng)
        try:
            i, j = h3.cell_to_local_ij(anchor, h)
        except Exception:  # pragma: no cover - pentagon distortion edge case
            i, j = int(x // 10), int(y // 10)
        stripe = ((i + 2 * j) // stripe_width) % len(rotation)
        specs.append(
            HexCellSpec(
                h3=h, lat=lat, lng=lng, x=round(x, 3), y=round(y, 3),
                crop_type=rotation[stripe],
                soil_moisture=round(min(1.0, max(0.05, rng.gauss(0.5, 0.15))), 3),
            )
        )
    known = set(cells_idx)
    for s in specs:
        s.neighbors = [n for n in h3.grid_ring(s.h3, 1) if n in known]

    return HexGrid(
        resolution=res, origin=origin, area_m2=area,
        edge_m=h3.average_hexagon_edge_length(res, unit="m"), cells=specs,
    )


def cell_boundary_geojson(h: str) -> list[list[float]]:
    ring = [[lng, lat] for lat, lng in h3.cell_to_boundary(h)]
    ring.append(ring[0])
    return ring


def hexgrid_feature_collection(cells, extra=None) -> dict:
    """Serialise ORM or spec cells into a GeoJSON FeatureCollection for MapLibre."""
    features = []
    for c in cells:
        props = {
            "h3": getattr(c, "h3", None) or getattr(c, "h3_index", None),
            "kind": c.kind, "crop_type": c.crop_type, "state": c.state, "bed_id": getattr(c, "bed_id", None),
            "weeds": bool(getattr(c, "weeds", False)),
            "soil_moisture": getattr(c, "soil_moisture", 0.5),
            "health": getattr(c, "health", 1.0), "x": c.x, "y": c.y,
        }
        if extra:
            props.update(extra(c))
        features.append({
            "type": "Feature", "id": props["h3"],
            "geometry": {"type": "Polygon", "coordinates": [cell_boundary_geojson(props["h3"])]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": features}


def grid_distance(a: str, b: str) -> int:
    try:
        return h3.grid_distance(a, b)
    except Exception:
        return 10**6
