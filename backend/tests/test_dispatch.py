"""Controlled-traffic rules for every kind of ground order the dispatcher can issue."""
import math

from shapely.geometry import LineString, Point

from app.services.dispatcher import ground_route, section_mission
from tests.farmkit import demo


def _line(start, wps):
    return LineString([start] + [(w["x"], w["y"]) for w in wps])


def _yard(d):
    s = d.plan.storage[0]
    return (s.x, s.y)


def test_moves_never_cross_beds():
    d = demo()
    start = _yard(d)
    crop = d.crop.buffer(-0.05)
    for sec in list(d.secs.values())[::7]:
        wps, length = ground_route(d.farm, *start, sec.x, sec.y)  # a goal inside a bed snaps to a track
        assert wps and math.isfinite(length)
        assert _line(start, wps).intersection(crop).length == 0.0, sec.id
        assert not crop.contains(Point(wps[-1]["x"], wps[-1]["y"]))


def test_seed_and_weed_work_from_the_tramline():
    d = demo()
    start = _yard(d)
    crop = d.crop.buffer(-0.05)
    for kind in ("seed", "weed"):
        for sec in list(d.secs.values())[::11]:
            wps, pass_from, _ = section_mission(d.farm, sec, *start, kind)
            line = _line(start, wps)
            assert line.intersection(crop).length == 0.0, (kind, sec.id)
            ps = LineString([(wps[pass_from]["x"], wps[pass_from]["y"]), (wps[-1]["x"], wps[-1]["y"])])
            assert abs(ps.length - sec.length) < 0.5  # the pass covers the whole section
            assert d.polys[sec.id].distance(ps) < 1.3  # right beside the bed


def test_harvest_is_the_only_pass_on_a_bed():
    d = demo()
    start = _yard(d)
    crop = d.crop.buffer(-0.05)
    for sec in list(d.secs.values())[::11]:
        wps, pass_from, _ = section_mission(d.farm, sec, *start, "harvest")
        approach = _line(start, wps[: pass_from + 1])
        assert approach.intersection(crop).length == 0.0, sec.id  # reaching it: tracks only
        cut = LineString([(w["x"], w["y"]) for w in wps[pass_from:]])
        others = crop.difference(d.polys[sec.id].buffer(0.05))
        assert cut.intersection(others).length == 0.0, sec.id  # never over a neighbouring section
        on_bed = cut.intersection(d.polys[sec.id]).length
        assert on_bed > sec.length * 0.9, (sec.id, on_bed)  # down the bed it harvests
        assert not crop.contains(Point(wps[-1]["x"], wps[-1]["y"]))  # and back onto the tramline
