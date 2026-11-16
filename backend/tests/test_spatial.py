import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from app.services.geo import Origin, polygon_area_m2, polygon_from_geojson
from app.services.field import field_from_geojson
from app.services.layout import LayoutParams, generate_layout
from app.services.planner import kmeans, plan_infrastructure
from app.services.track_network import TrackNetwork
from app.services.world_builder import build_world_config

RECT = {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[
    [-119.80, 36.70], [-119.797, 36.70], [-119.797, 36.7022], [-119.80, 36.7022], [-119.80, 36.70]]]}}


def _field_xy(fld):
    return fld.polygon_xy


def test_origin_roundtrip():
    o = Origin(36.7, -119.8)
    x, y = o.to_xy(36.7011, -119.7985)
    lat, lng = o.to_latlng(x, y)
    assert abs(lat - 36.7011) < 1e-9 and abs(lng + 119.7985) < 1e-9


def test_field_geometry():
    area = polygon_area_m2(polygon_from_geojson(RECT))
    assert 60_000 < area < 70_000
    fld = field_from_geojson(RECT)
    assert abs(fld.area_m2 - area) < 1.0
    assert abs(fld.polygon_xy.area - area) / area < 0.01  # ENU polygon matches the geodesic area
    c = fld.polygon_xy.centroid
    assert abs(c.x) < 1.0 and abs(c.y) < 1.0  # origin sits on the field centroid


def test_kmeans_converges():
    rng = np.random.default_rng(0)
    pts = np.vstack([rng.normal((0, 0), 1, (50, 2)), rng.normal((20, 20), 1, (50, 2))])
    res = kmeans(pts, 2, seed=0)
    assert res.converged
    assert sorted(np.round(res.centroids.sum(axis=1) / 2).astype(int).tolist()) == [0, 20]


def test_controlled_traffic_layout():
    grid = field_from_geojson(RECT)
    field = _field_xy(grid)
    L = generate_layout(field, LayoutParams(bed_width=6.0, lane_width=2.4, headland_width=10.0))
    assert len(L.sections) > 10 and len(L.beds) > 5
    beds = [Polygon(s.polygon) for s in L.sections]
    crop = unary_union(beds)
    # beds stay inside the field, clear of the headland
    assert field.buffer(-L.params.headland_width + 0.5).contains(crop)
    # tracks are never on crops (allow a touching edge)
    for t in L.tracks:
        assert LineString(t.coords).intersection(crop.buffer(-0.05)).length == 0.0, t.id
    # every work pass runs on a tramline right beside its bed, not on it
    for s in L.sections:
        for ps in s.passes:
            seg = LineString([ps["start"], ps["end"]])
            assert seg.intersection(crop.buffer(-0.05)).length == 0.0
            assert Polygon(s.polygon).distance(seg) < L.params.lane_width / 2 + 0.01
    assert 0.4 < L.crop_area_m2 / L.field_area_m2 < 0.85


def test_track_network_never_crosses_beds():
    grid = field_from_geojson(RECT)
    L = generate_layout(_field_xy(grid), LayoutParams())
    net = TrackNetwork([{"coords": t.coords} for t in L.tracks])
    crop = unary_union([Polygon(s.polygon) for s in L.sections]).buffer(-0.05)
    a, b = L.sections[0], L.sections[-1]
    pts, length = net.route(*a.passes[0]["start"], *b.passes[1]["end"])
    assert len(pts) >= 2 and np.isfinite(length)
    assert LineString(pts).intersection(crop).length == 0.0
    # a goal inside a bed snaps onto a track
    snapped, _, _ = net.snap(a.x, a.y)
    assert not crop.contains(Point(snapped))


def test_plan_and_world_config():
    grid = field_from_geojson(RECT)
    plan = plan_infrastructure(grid)
    field = _field_xy(grid)
    assert plan.k_docks >= 1 and plan.storage and plan.repair[0].kind == "repair_station"
    for s in plan.sites:  # the yard is outside the cropped field
        assert not field.contains(Point(s.x, s.y)), s.slug
    w = build_world_config("f", "Farm", grid, plan)
    assert w["schema"] == 4 and len(w["sections"]) == len(plan.layout.sections)
    assert "hex" not in w
    assert {t["kind"] for t in w["tracks"]} >= {"headland", "lane", "spur"}
    active = [r for r in w["robots"] if r["active"]]
    assert len(active) <= w["limits"]["max_active_robots"]
    # the on-board track graph is one connected network that reaches every yard pad
    g = w["track_graph"]
    adj: dict[int, set[int]] = {}
    for a, b in g["edges"]:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, todo = {0}, [0]
    while todo:
        for v in adj.get(todo.pop(), ()):
            if v not in seen:
                seen.add(v)
                todo.append(v)
    assert len(seen) == len(g["nodes"])
    pads = {(round(s.x, 2), round(s.y, 2)) for s in plan.sites}
    assert pads <= {(round(x, 2), round(y, 2)) for x, y in g["nodes"]}
