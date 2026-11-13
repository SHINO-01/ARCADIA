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
