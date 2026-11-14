from __future__ import annotations

import random

from fastapi import APIRouter, HTTPException

from app.schemas import ChaosConfig, ChaosInject
from app.services import chaos as chaos_svc
from app.services import fleet

router = APIRouter(prefix="/chaos", tags=["chaos"])


@router.get("/config", response_model=ChaosConfig)
async def get_config():
    return await chaos_svc.get_config()


@router.put("/config", response_model=ChaosConfig)
async def put_config(cfg: ChaosConfig):
    cfg = await chaos_svc.set_config(cfg)
    await fleet.publish_dashboard({"t": "chaos_config", **cfg.model_dump()})
    return cfg


PRESETS = [
    {"id": "strand_ugv_jam", "label": "Strand UGV · motor jam", "robot_type": "seeder_ugv", "kind": "motor_jam"},
    {"id": "strand_ugv_mud", "label": "Strand UGV · terrain entrapment", "robot_type": "seeder_ugv", "kind": "stuck_terrain"},
    {"id": "ugv_battery", "label": "UGV battery collapse", "robot_type": "seeder_ugv", "kind": "battery_drop"},
    {"id": "uav_battery", "label": "Ground UAV · battery collapse", "robot_type": "scout_uav", "kind": "battery_drop"},
    {"id": "uav_latency", "label": "UAV uplink latency", "robot_type": "scout_uav", "kind": "network_latency"},
    {"id": "uav_glitch", "label": "UAV localisation glitch", "robot_type": "scout_uav", "kind": "sensor_glitch"},
]


@router.get("/presets")
async def presets():
    return PRESETS


@router.post("/presets/{preset_id}")
async def run_preset(preset_id: str):
    p = next((x for x in PRESETS if x["id"] == preset_id), None)
    if p is None:
        raise HTTPException(404, "unknown preset")
    live = await fleet.get_fleet()
    targets = [rid for rid, r in live.items() if r.get("type") == p["robot_type"] and r.get("active", True)
               and not r.get("fault") and r.get("status") not in ("offline",)]
    if not targets:
        raise HTTPException(409, f"no eligible {p['robot_type']} in the fleet")
    robot_id = targets[0]
    cmd = chaos_svc.build_fault_command(robot_id, p["kind"], None, robot_type=p["robot_type"])
    cmd.payload["source"] = "operator"
    await fleet.send_command(cmd)
    await fleet.publish_dashboard({"t": "chaos", "robot_id": robot_id, "fault": cmd.payload["fault"], "params": cmd.payload["params"]})
    return {"robot_id": robot_id, **cmd.payload}


@router.post("/inject")
async def inject(body: ChaosInject):
    live = await fleet.get_fleet()
    robot_id = body.robot_id
    if robot_id is None:
        targets = chaos_svc.eligible_targets(live)
        if not targets:
            raise HTTPException(409, "no eligible robots (all faulted or offline)")
        robot_id = random.choice(targets)
    elif robot_id not in live:
        raise HTTPException(404, "robot not found in live fleet")
    cmd = chaos_svc.build_fault_command(robot_id, body.kind, body.params or None, robot_type=live[robot_id].get("type"))
    cmd.payload["source"] = "operator"
    await fleet.send_command(cmd)
    await fleet.publish_dashboard({"t": "chaos", "robot_id": robot_id, "fault": cmd.payload["fault"], "params": cmd.payload["params"]})
    return {"robot_id": robot_id, **cmd.payload}
