"""Every operation end to end in the headless twin, with the controlled-traffic rule checked on
every telemetry frame: ground units are never on a bed, except a harvester on its harvest pass.
(Crop truth and yields live in the world model - see test_agro.py.)"""
import asyncio

import pytest
from shapely.geometry import Point

from app.bridge.headless_sim import HeadlessTwin
from app.schemas import Command
from app.services.dispatcher import ground_route, section_mission
from tests.farmkit import demo


async def _until(events, pred, steps=2000):
    for _ in range(steps):
        await asyncio.sleep(0.01)
        if pred(events):
            return True
    return False


def _done(mid):
    return lambda ev: any(e.kind in ("mission_complete", "mission_failed") and e.mission_id == mid for e in ev)


def _mission(mid, kind, wps, **extra):
    return {"mission_id": mid, "kind": kind, "waypoints": wps, **extra}


@pytest.mark.asyncio
async def test_every_operation_respects_the_beds():
    d = demo()
    frames, events, violations, cutting = [], [], [], []
    harvest_pass: dict[str, str] = {}  # mission_id -> section it may drive on
    crop = d.crop.buffer(-0.3)

    async def on_t(f):
        frames.append(f)
        if f.type == "scout_uav" or f.status in ("towed",):
            return
        p = Point(f.x, f.y)
        if not crop.contains(p):
            return
        sec = harvest_pass.get(f.mission_id or "")
        if sec and d.polys[sec].buffer(0.3).contains(p):
            cutting.append(f.mission_id)
            return
        violations.append((f.robot_id, f.mission_id, round(f.x, 1), round(f.y, 1)))

    async def on_e(e):
        events.append(e)

    twin = HeadlessTwin(on_t, on_e, hz=50.0, telemetry_hz=25.0)
    twin.load_world(d.world)
    twin.speed_factor = 40.0
    twin.start()
    ugv, agv = twin.robots["ugv-1"], twin.robots["agv-1"]
    secs = list(d.secs.values())
    a = secs[3]
    try:
        # survey: the scout flies straight over the beds (airborne, 6 m up)
        await twin.handle_command(Command(kind="mission", robot_id="uav-1", payload=_mission(
            "survey", "survey", [{"x": s.x, "y": s.y, "z": 6.0} for s in secs[:6]])))
        # seed section a from its tramline
        wps, pf, _ = section_mission(d.farm, a, ugv.x, ugv.y, "seed")
        await twin.handle_command(Command(kind="mission", robot_id="ugv-1", payload=_mission(
            "seed", "seed", wps, section=a.id, pass_from=pf)))
        assert await _until(events, _done("seed")), [e.kind for e in events][-8:]
        assert any(e.kind == "seeded" and e.section == a.id for e in events)
        assert await _until(events, _done("survey"))
        seen = {e.section for e in events if e.kind == "section_seen"}
        assert {s.id for s in secs[:6]} <= seen  # mock CV: every overflown section was in view

        # weed section a (now planted) from the other end
        for kind in ("fertilise", "weed", "spray"):
            wps, pf, _ = section_mission(d.farm, a, ugv.x, ugv.y, kind)
            await twin.handle_command(Command(kind="mission", robot_id="ugv-1", payload=_mission(
                kind, kind, wps, section=a.id, pass_from=pf)))
            assert await _until(events, _done(kind)), kind
        assert {"fertilised", "weeded", "sprayed"} <= {e.kind for e in events if e.section == a.id}

        # harvest: let the crop ripen, then the one pass that drives on a bed
        wps, pf, _ = section_mission(d.farm, a, ugv.x, ugv.y, "harvest")
        harvest_pass["harvest"] = a.id
        await twin.handle_command(Command(kind="mission", robot_id="ugv-1", payload=_mission(
            "harvest", "harvest", wps, section=a.id, pass_from=pf)))
        assert await _until(events, _done("harvest"))
        assert any(e.kind == "harvested" and e.section == a.id for e in events)

        # goto aimed at the middle of a bed: snapped onto a track
        wps, _ = ground_route(d.farm, ugv.x, ugv.y, secs[-5].x, secs[-5].y)
        await twin.handle_command(Command(kind="mission", robot_id="ugv-1", payload=_mission("goto", "goto", wps)))
        assert await _until(events, _done("goto"))

        # ground patrol: two clicks inside beds, looped; each leg on the tracks
        legs, cx, cy = [], ugv.x, ugv.y
        for s in (secs[10], secs[40], secs[10]):
            leg, _ = ground_route(d.farm, cx, cy, s.x, s.y)
            legs += leg
            cx, cy = leg[-1]["x"], leg[-1]["y"]
        await twin.handle_command(Command(kind="mission", robot_id="ugv-1", payload=_mission("patrol", "patrol", legs, loop=True)))
        assert await _until(events, lambda ev: twin.robots["ugv-1"].mission and twin.robots["ugv-1"].mission.get("_laps", 0) >= 1)
        await twin.handle_command(Command(kind="cancel", robot_id="ugv-1"))

        # return home: nearest dock along the tracks, then charge
        await twin.handle_command(Command(kind="return_home", robot_id="ugv-1", payload={"mission_id": "home"}))
        assert await _until(events, lambda ev: any(e.kind == "charging_started" and e.robot_id == "ugv-1" for e in ev))

        # breakdown + tow, planned from a STALE AGV position (the far corner of the field): the
        # unit still joins the tracks from where it really is and never crosses a bed
        await asyncio.sleep(0.5)
        far = secs[-1]
        wps, _ = ground_route(d.farm, ugv.x, ugv.y, far.passes[0]["start"][0], far.passes[0]["start"][1])
        await twin.handle_command(Command(kind="mission", robot_id="ugv-1", payload=_mission("out", "goto", wps)))
        assert await _until(events, _done("out"))
        await twin.handle_command(Command(kind="chaos", robot_id="ugv-1", payload={"fault": "motor_jam", "params": {}}))
        await twin.handle_command(Command(kind="set_active", robot_id="agv-1", payload={"active": True}))
        stale = d.secs[secs[0].id]
        to_unit, _ = ground_route(d.farm, stale.passes[0]["end"][0], stale.passes[0]["end"][1], ugv.x, ugv.y)
        rep = d.plan.repair[0]
        to_bay, _ = ground_route(d.farm, ugv.x, ugv.y, rep.x, rep.y)
        await twin.handle_command(Command(kind="tow", robot_id="agv-1", payload=_mission(
            "tow", "tow", to_unit + to_bay, target_robot="ugv-1", pickup_index=len(to_unit) - 1)))
        assert await _until(events, lambda ev: any(e.kind == "tow_complete" for e in ev), steps=3000)
        assert any(e.kind == "tow_attached" for e in events)
        assert abs(agv.x - rep.x) < 4 and abs(agv.y - rep.y) < 4
    finally:
        await twin.stop()
    assert not violations, violations[:10]
    assert cutting.count("harvest") > 5  # the check sees bed frames: the harvester really drove the bed
    assert len(frames) > 200
