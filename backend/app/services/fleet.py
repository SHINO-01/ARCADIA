"""Fleet state cache (Redis) and dashboard fan-out. Async flavour for the API process,
sync flavour for Celery workers. Postgres holds the durable copy (see persistence)."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.core.redis import (
    CH_COMMANDS, CH_DASHBOARD, KEY_ACTIVE_FARM, KEY_DISPATCH_PAUSED, KEY_FLEET, get_redis, get_sync_redis,
)
from app.schemas import Command


def _dumps(o: Any) -> str:
    return json.dumps(o, separators=(",", ":"), default=str)


# ------------------------------------------------------------------ async (API process)
async def get_fleet() -> dict[str, dict]:
    raw = await get_redis().hgetall(KEY_FLEET)
    return {k: json.loads(v) for k, v in raw.items()}


async def get_robot(robot_id: str) -> dict | None:
    raw = await get_redis().hget(KEY_FLEET, robot_id)
    return json.loads(raw) if raw else None


async def update_robot(robot_id: str, broadcast: bool = True, **fields) -> dict:
    r = get_redis()
    raw = await r.hget(KEY_FLEET, robot_id)
    state = json.loads(raw) if raw else {"id": robot_id}
    state.update(fields)
    state["updated"] = time.time()
    await r.hset(KEY_FLEET, robot_id, _dumps(state))
    if broadcast:
        await publish_dashboard({"t": "fleet", "robot": state})
    return state


async def set_fleet(robots: list[dict]) -> None:
    r = get_redis()
    await r.delete(KEY_FLEET)
    if robots:
        await r.hset(KEY_FLEET, mapping={rb["id"]: _dumps(rb) for rb in robots})
    await publish_dashboard({"t": "fleet_reset", "robots": robots})


async def publish_dashboard(msg: dict) -> None:
    await get_redis().publish(CH_DASHBOARD, _dumps(msg))


async def send_command(cmd: Command) -> str:
    cmd.command_id = cmd.command_id or uuid.uuid4().hex[:12]
    await get_redis().publish(CH_COMMANDS, cmd.model_dump_json())
    return cmd.command_id


async def get_active_farm_id() -> str | None:
    return await get_redis().get(KEY_ACTIVE_FARM)


async def set_active_farm_id(farm_id: str | None) -> None:
    r = get_redis()
    if farm_id:
        await r.set(KEY_ACTIVE_FARM, farm_id)
    else:
        await r.delete(KEY_ACTIVE_FARM)


async def is_dispatch_paused() -> bool:
    return (await get_redis().get(KEY_DISPATCH_PAUSED)) == "1"


async def set_dispatch_paused(paused: bool) -> None:
    await get_redis().set(KEY_DISPATCH_PAUSED, "1" if paused else "0")
    await publish_dashboard({"t": "dispatch", "paused": paused})


# ------------------------------------------------------------------ sync (Celery workers)
def sync_get_fleet() -> dict[str, dict]:
    raw = get_sync_redis().hgetall(KEY_FLEET)
    return {k: json.loads(v) for k, v in raw.items()}


def sync_update_robot(robot_id: str, broadcast: bool = True, **fields) -> dict:
    r = get_sync_redis()
    raw = r.hget(KEY_FLEET, robot_id)
    state = json.loads(raw) if raw else {"id": robot_id}
    state.update(fields)
    state["updated"] = time.time()
    r.hset(KEY_FLEET, robot_id, _dumps(state))
    if broadcast:
        sync_publish_dashboard({"t": "fleet", "robot": state})
    return state


def sync_publish_dashboard(msg: dict) -> None:
    get_sync_redis().publish(CH_DASHBOARD, _dumps(msg))


def sync_send_command(cmd: Command) -> str:
    cmd.command_id = cmd.command_id or uuid.uuid4().hex[:12]
    get_sync_redis().publish(CH_COMMANDS, cmd.model_dump_json())
    return cmd.command_id


def sync_active_farm_id() -> str | None:
    return get_sync_redis().get(KEY_ACTIVE_FARM)


def sync_dispatch_paused() -> bool:
    return get_sync_redis().get(KEY_DISPATCH_PAUSED) == "1"
