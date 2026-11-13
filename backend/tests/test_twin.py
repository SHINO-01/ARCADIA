import asyncio

import pytest

from app.bridge.headless_sim import HeadlessTwin
from app.schemas import Command
from app.services.h3_engine import build_hexgrid
from app.services.planner import plan_infrastructure
from app.services.world_builder import build_world_config
from tests.test_spatial import RECT


@pytest.mark.asyncio
async def test_twin_runs_survey_and_seed_missions():
    frames, events = [], []

    async def on_t(f):
        frames.append(f)

    async def on_e(e):
        events.append(e)

    g = build_hexgrid(RECT)
    world = build_world_config("f", "Farm", g, plan_infrastructure(g))
    twin = HeadlessTwin(on_t, on_e, hz=50.0, telemetry_hz=10.0)
    twin.load_world(world)
    twin.speed_factor = 40.0
    twin.start()
    try:
        cells = [c for c in world["hex"]["cells"] if c["kind"] == "crop"][:6]
        await twin.handle_command(Command(kind="mission", robot_id="uav-1", payload={
            "mission_id": "m1", "kind": "survey", "waypoints": [{"x": c["x"], "y": c["y"], "z": 6.0} for c in cells]}))
        await twin.handle_command(Command(kind="mission", robot_id="ugv-1", payload={
            "mission_id": "m2", "kind": "seed", "waypoints": [{"x": cells[0]["x"], "y": cells[0]["y"], "z": 0.0, "h3": cells[0]["h3"]}]}))
        for _ in range(400):
            await asyncio.sleep(0.05)
            kinds = {e.kind for e in events}
            if {"mission_complete", "seeded", "cell_observed"} <= kinds and \
                    len([e for e in events if e.kind == "mission_complete"]) >= 2:
                break
        kinds = [e.kind for e in events]
        assert "mission_started" in kinds
        assert "cell_observed" in kinds, kinds
        assert "seeded" in kinds, kinds
        assert kinds.count("mission_complete") >= 2, kinds
        assert any(f.robot_id == "uav-1" and f.speed > 0 for f in frames)
        # chaos: motor jam strands the UGV, tow recovers it
        await twin.handle_command(Command(kind="chaos", robot_id="ugv-1", payload={"fault": "motor_jam", "params": {}}))
        assert events[-1].kind == "fault_detected"
        await twin.handle_command(Command(kind="set_active", robot_id="agv-1", payload={"active": True}))
        await twin.handle_command(Command(kind="tow", robot_id="agv-1", payload={"mission_id": "m3", "target_robot": "ugv-1"}))
        for _ in range(600):
            await asyncio.sleep(0.05)
            if any(e.kind == "tow_complete" for e in events):
                break
        assert any(e.kind == "tow_attached" for e in events)
        assert any(e.kind == "tow_complete" for e in events), [e.kind for e in events][-10:]
    finally:
        await twin.stop()
