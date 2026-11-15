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
    on_road = sum(1 for w in wps[:-1] if w.h3 in road)
    assert on_road >= len(wps[:-1]) // 2  # the route mostly follows roads
    # and it is never a wild detour
    import math
    length = math.hypot(wps[0].x - a.x, wps[0].y - a.y) + sum(math.hypot(wps[i + 1].x - wps[i].x, wps[i + 1].y - wps[i].y) for i in range(len(wps) - 1))
    assert length < 2.0 * math.hypot(b.x - a.x, b.y - a.y) + 40
