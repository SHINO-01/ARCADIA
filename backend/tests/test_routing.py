from app.services.h3_engine import build_hexgrid
from app.services.planner import plan_infrastructure
from app.services.routing import RoadNetwork
from tests.test_spatial import RECT


def test_ground_route_uses_roads():
    g = build_hexgrid(RECT)
    plan = plan_infrastructure(g)
    road = set(plan.roads)
    cells = [{"h3": c.h3, "x": c.x, "y": c.y, "lat": c.lat, "lng": c.lng, "kind": "road" if c.h3 in road else c.kind} for c in g.cells]
    net = RoadNetwork(cells)
    crops = [c for c in g.cells if c.h3 not in road and c.kind == "crop"]
    a, b = crops[0], crops[-1]
    wps = net.route(a.x, a.y, {"x": b.x, "y": b.y, "h3": b.h3})
    assert len(wps) >= 3
    assert wps[-1].x == b.x and wps[-1].y == b.y
    assert all(w.h3 in road for w in wps[:-1])  # every intermediate waypoint sits on a road
