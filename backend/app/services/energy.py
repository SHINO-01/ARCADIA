"""Renewable microgrid model for the charging docks.

Each dock is a self-sustaining solar or wind microgrid with battery storage. Sun, wind and the
pace of time come from the agronomic world model: 1:1 on the real-time twin, accelerated in the
simulation sandbox. Without a running world model a compressed day/night cycle (DAY_LENGTH_S real
seconds per day) and a seeded Ornstein-Uhlenbeck wind stand in. Robots charging draw from storage.
"""
from __future__ import annotations

import json
import math
import random
import time

from app.core.redis import KEY_ENERGY, get_sync_redis

DAY_LENGTH_S = 600.0
MAX_STORAGE_SCALE = 60.0  # storage integrates at most 60x real time (a fast sandbox would swing it empty/full)
CHARGE_DRAW_KW = {"scout_uav": 0.6, "seeder_ugv": 3.2, "cropcare_ugv": 2.2, "harvester_ugv": 3.6, "logistics_agv": 4.5}


def sim_hour(now: float | None = None) -> float:
    t = (now or time.time()) % DAY_LENGTH_S
    return 24.0 * t / DAY_LENGTH_S


def irradiance(hour: float) -> float:
    """0..1 clear-sky irradiance curve with sunrise 6h, sunset 18h."""
    return max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0)) if 6.0 <= hour <= 18.0 else 0.0


class MicrogridModel:
    def __init__(self, seed: int = 3) -> None:
        self.rng = random.Random(seed)
        self.wind = 6.0

    def step_wind(self, dt: float) -> float:
        # Ornstein-Uhlenbeck around 6.5 m/s, clipped to turbine envelope
        self.wind += (6.5 - self.wind) * 0.05 * dt + self.rng.gauss(0, 0.9) * math.sqrt(dt)
        self.wind = max(0.0, min(22.0, self.wind))
        return self.wind

    @staticmethod
    def wind_power_fraction(v: float) -> float:
        if v < 3.0 or v > 20.0:
            return 0.0
        if v >= 12.0:
            return 1.0
        return ((v - 3.0) / 9.0) ** 3


def tick(infra: list[dict], fleet: dict[str, dict], dt: float, model: MicrogridModel) -> list[dict]:
    """Advance every dock by dt seconds. Returns the new per-dock readings (also cached in Redis).
    Sun and wind come from the agronomic world model's weather when it is running."""
    r = get_sync_redis()
    now = r.get("arcadia:world:now")
    if now:
        w = json.loads(now)
        hour, irr = float(w["hour"]), float(w["irradiance"])
        wind = model.wind = 0.7 * model.wind + 0.3 * float(w.get("wind", model.wind))
        scale = min(MAX_STORAGE_SCALE, float(w.get("scale", 1.0)))
    else:
        hour = sim_hour()
        irr = irradiance(hour)
        wind = model.step_wind(dt)
        scale = MAX_STORAGE_SCALE
    out = []
    for site in infra:
        if not site["kind"].endswith("dock"):
            continue
        cap = site["capacity_kw"]
        gen = cap * (irr * (0.85 + 0.15 * math.sin(time.time() / 7.0)) if site["kind"] == "solar_dock"
                     else model.wind_power_fraction(wind))
        consumed = 0.25  # dock base load (controllers, telemetry uplink)
        for rb in fleet.values():
            if rb.get("status") == "charging" and (rb.get("home") or rb.get("home_dock")) == site["slug"]:
                consumed += CHARGE_DRAW_KW.get(rb.get("type", ""), 2.0)
        prev = r.hget(KEY_ENERGY, site["slug"])
        stored = json.loads(prev)["stored_kwh"] if prev else site.get("stored_kwh", 40.0)
        stored = max(0.0, min(site["storage_kwh"], stored + (gen - consumed) * dt * scale / 3600.0))
        reading = {"slug": site["slug"], "kind": site["kind"], "generated_kw": round(gen, 3),
                   "consumed_kw": round(consumed, 3), "stored_kwh": round(stored, 3),
                   "storage_kwh": site["storage_kwh"], "irradiance": round(irr, 3), "wind_ms": round(wind, 2),
                   "sim_hour": round(hour, 2), "soc": round(stored / max(site["storage_kwh"], 1e-6), 3), "t": time.time()}
        r.hset(KEY_ENERGY, site["slug"], json.dumps(reading))
        out.append(reading)
    return out
