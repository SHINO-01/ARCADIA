"""Chaos Engine: randomly injects mechanical, electrical and network faults into the fleet."""
from __future__ import annotations

import json
import random

from app.core.config import settings
from app.core.redis import KEY_CHAOS_CONFIG, get_redis, get_sync_redis
from app.schemas import ChaosConfig, Command

FAULT_PARAMS = {
    "motor_jam": lambda rng: {"wheel": rng.choice(["front_left", "front_right", "rear_left", "rear_right"]),
                              "torque_spike_nm": round(rng.uniform(40, 95), 1)},
    "battery_drop": lambda rng: {"level_pct": round(rng.uniform(1.0, 6.0), 1), "cell_fault": rng.randint(1, 14)},
    "network_latency": lambda rng: {"latency_ms": rng.choice([900, 1500, 2400, 4000]),
                                    "packet_loss": round(rng.uniform(0.05, 0.4), 2)},
    "sensor_glitch": lambda rng: {"sensor": rng.choice(["gps", "imu", "lidar"]), "bias_m": round(rng.uniform(0.3, 1.5), 2)},
    "stuck_terrain": lambda rng: {"terrain": rng.choice(["waterlogged bed", "irrigation furrow", "soft shoulder"]),
                                  "wheel_slip": round(rng.uniform(0.6, 0.95), 2)},
}


# Faults that make physical sense per platform (a UAV has no drive motors to jam).
FAULTS_BY_TYPE = {
    "scout_uav": ["battery_drop", "network_latency", "sensor_glitch"],
    "logistics_agv": ["motor_jam", "battery_drop", "network_latency", "sensor_glitch", "stuck_terrain"],
}


def allowed_faults(robot_type: str | None, allowed: list[str] | None = None) -> list[str]:
    kinds = FAULTS_BY_TYPE.get(robot_type or "", list(FAULT_PARAMS))
    if allowed:
        kinds = [k for k in kinds if k in allowed] or kinds
    return kinds


def default_config() -> ChaosConfig:
    return ChaosConfig(enabled=settings.chaos_enabled, interval_s=settings.chaos_interval_s,
                       probability=settings.chaos_probability)


def sync_get_config() -> ChaosConfig:
    raw = get_sync_redis().get(KEY_CHAOS_CONFIG)
    return ChaosConfig(**json.loads(raw)) if raw else default_config()


async def get_config() -> ChaosConfig:
    raw = await get_redis().get(KEY_CHAOS_CONFIG)
    return ChaosConfig(**json.loads(raw)) if raw else default_config()


async def set_config(cfg: ChaosConfig) -> ChaosConfig:
    await get_redis().set(KEY_CHAOS_CONFIG, cfg.model_dump_json())
    return cfg


def build_fault_command(robot_id: str, kind: str | None, params: dict | None, rng: random.Random | None = None,
                        allowed: list[str] | None = None, robot_type: str | None = None) -> Command:
    rng = rng or random.Random()
    kind = kind or rng.choice(allowed_faults(robot_type, allowed))
    params = params or FAULT_PARAMS[kind](rng)
    return Command(kind="chaos", robot_id=robot_id, payload={"fault": kind, "params": params, "source": "chaos_engine"})


def eligible_targets(fleet: dict[str, dict]) -> list[str]:
    return [rid for rid, r in fleet.items()
            if r.get("active", True) and r.get("status") in ("active", "idle", "returning") and not r.get("fault")]
