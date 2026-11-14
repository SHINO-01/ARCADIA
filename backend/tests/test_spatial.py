import h3

from app.services.geo import Origin, polygon_area_m2, polygon_from_geojson
from app.services.h3_engine import build_hexgrid, choose_resolution, hexgrid_feature_collection
from app.services.planner import kmeans, plan_infrastructure
from app.services.world_builder import build_world_config
import numpy as np

RECT = {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[
    [-119.80, 36.70], [-119.797, 36.70], [-119.797, 36.7022], [-119.80, 36.7022], [-119.80, 36.70]]]}}


def test_origin_roundtrip():
    o = Origin(36.7, -119.8)
    x, y = o.to_xy(36.7011, -119.7985)
    lat, lng = o.to_latlng(x, y)
    assert abs(lat - 36.7011) < 1e-9 and abs(lng + 119.7985) < 1e-9
    assert 120 < y < 125 and 130 < x < 136


def test_area_and_resolution():
    poly = polygon_from_geojson(RECT)
    area = polygon_area_m2(poly)
    assert 60_000 < area < 70_000
    res = choose_resolution(area, target_cells=300)
    assert res in (12, 13)


def test_hexgrid_covers_polygon_and_is_deterministic():
    g1 = build_hexgrid(RECT, seed=1)
    g2 = build_hexgrid(RECT, seed=1)
    assert [c.h3 for c in g1.cells] == [c.h3 for c in g2.cells]
    assert [c.crop_type for c in g1.cells] == [c.crop_type for c in g2.cells]
    assert len(g1.cells) > 100
    assert all(h3.is_valid_cell(c.h3) for c in g1.cells)
    assert len({c.crop_type for c in g1.cells}) >= 3
    fc = hexgrid_feature_collection(g1.cells)
    assert fc["features"][0]["geometry"]["type"] == "Polygon"
    assert len(fc["features"][0]["geometry"]["coordinates"][0]) == 7  # closed hexagon


def test_kmeans_converges():
    rng = np.random.default_rng(0)
    pts = np.vstack([rng.normal((0, 0), 1, (50, 2)), rng.normal((20, 20), 1, (50, 2))])
    res = kmeans(pts, 2, seed=0)
    assert res.converged
    assert sorted(np.round(res.centroids.sum(axis=1) / 2).astype(int).tolist()) == [0, 20]


def test_plan_and_world_config():
    g = build_hexgrid(RECT)
    plan = plan_infrastructure(g)
    # repositioning: a different orientation / spacing yields a different road layout
    alt = plan_infrastructure(build_hexgrid(RECT), orientation="ns", road_spacing=8, bed_width=2)
    assert set(alt.roads) != set(plan.roads) and alt.orientation == "ns"
    assert plan.k_docks >= 1 and len(plan.storage) >= 1
    sites = {s.h3 for s in plan.docks + plan.storage}
    assert len(sites) == len(plan.docks) + len(plan.storage)  # unique cells
    assert plan.mean_travel_m < plan.max_travel_m
    assert len(plan.roads) > 10 and len(plan.beds) >= 2 and plan.repair[0].kind == "repair_station"
    assert all(h not in sites for h in plan.roads)
    w = build_world_config("f", "Farm", g, plan)
    assert w["schema"] == 2 and w["hex"]["count"] == len(g.cells)
    assert any(c["kind"] == "road" for c in w["hex"]["cells"]) and any(c["kind"] == "repair" for c in w["hex"]["cells"])
    assert any(c.get("bed") for c in w["hex"]["cells"])
    active = [r for r in w["robots"] if r["active"]]
    assert len(active) <= w["limits"]["max_active_robots"]
    assert any(r["type"] == "logistics_agv" and not r["active"] for r in w["robots"])
    kinds = {c["kind"] for c in w["hex"]["cells"]}
    assert {"crop", "dock", "storage", "road", "repair"} <= kinds
