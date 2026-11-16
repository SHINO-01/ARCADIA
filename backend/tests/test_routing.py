import math

from app.services.track_network import TrackNetwork


def test_grid_network_routes_around_a_block():
    # a 100 x 50 m block with tracks on its perimeter only: going corner to corner must follow the edge
    tracks = [{"coords": [[0, 0], [100, 0], [100, 50], [0, 50], [0, 0]]}]
    net = TrackNetwork(tracks)
    pts, length = net.route(0, 0, 100, 50)
    assert math.isclose(length, 150.0, abs_tol=0.5)
    assert (100.0, 0.0) in pts or (0.0, 50.0) in pts


def test_snap_and_same_segment():
    net = TrackNetwork([{"coords": [[0, 0], [100, 0]]}])
    pts, length = net.route(10, 3, 60, -2)  # both off-track, same segment
    assert math.isclose(length, 50.0, abs_tol=0.1)
    assert pts[0] == (10.0, 0.0) and pts[-1] == (60.0, 0.0)


def test_near_miss_spur_is_joined():
    # a yard spur that stops 0.3 mm short of the headland (millimetre rounding) must still connect
    head = {"coords": [[0, 0], [100, 0], [100, 50], [0, 50], [0, 0]]}
    spur = {"coords": [[60.0, -0.0003], [60.0, -20.0]]}
    net = TrackNetwork([head, spur])
    pts, length = net.route(0, 50, 60, -20)
    assert pts and math.isclose(length, 60 + 50 + 20, abs_tol=0.5)
    nodes = {tuple(n) for n in net.graph()["nodes"]}
    assert (60.0, -20.0) in nodes
