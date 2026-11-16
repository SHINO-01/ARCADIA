"""Sensor models: what the farm can actually measure about the (hidden) field state.

  * UAV multispectral pass over a section: NDVI (sigma 0.025), canopy water-stress index from
    thermal imagery (CWSI ~ 1 - Ks, sigma 0.06), weed and disease detection with logistic
    detection probabilities (small infestations are often missed), surface ponding, and a
    yield estimate with +/-12 % error. The Webots supervisor only decides *when* a section is
    seen (proximity); this module decides *what* is seen.
  * Soil-moisture probes at fixed sections: volumetric water content (sigma 0.012 m3/m3).
"""
from __future__ import annotations

import numpy as np

from app.agro.model import STAGE_NAMES, FieldModel


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def uav_observe(model: FieldModel, idx: np.ndarray, rng: np.random.Generator) -> list[dict]:
    idx = np.atleast_1d(np.asarray(idx, dtype=int))
    s = model.s
    ndvi = np.clip(model.ndvi()[0, idx] + rng.normal(0, 0.025, len(idx)), 0, 1)
    weed = s["weed"][0, idx]
    dis = s["disease"][0, idx]
    weed_seen = rng.random(len(idx)) < _sigmoid((weed - 0.08) / 0.025)
    dis_seen = rng.random(len(idx)) < _sigmoid((dis - 0.05) / 0.015)
    cwsi = np.clip(1 - s["ks"][0, idx] + rng.normal(0, 0.06, len(idx)), 0, 1)
    moist = np.clip(model.moisture()[0, idx] + rng.normal(0, 0.08, len(idx)), 0, 1.5)
    ypot = model.yield_potential()[0, idx] * np.clip(1 + rng.normal(0, 0.12, len(idx)), 0.5, 1.5)
    stage = s["stage"][0, idx]
    out = []
    for j, i in enumerate(idx):
        out.append({
            "i": int(i), "ndvi": round(float(ndvi[j]), 3), "stage": STAGE_NAMES[int(stage[j])],
            "weeds": bool(weed_seen[j]), "weed_cover": round(float(np.clip(weed[j] + rng.normal(0, 0.03), 0, 1)) if weed_seen[j] else 0.0, 3),
            "disease": round(float(np.clip(dis[j] + rng.normal(0, 0.02), 0, 1)) if dis_seen[j] else 0.0, 3),
            "cwsi": round(float(cwsi[j]), 3), "moisture": round(float(moist[j]), 3),
            "ponding": bool(s["pond"][0, i] > 3), "yield_est_t_ha": round(float(ypot[j]), 2),
            "health": round(float(np.clip(1 - 0.6 * cwsi[j] - 0.8 * (dis[j] if dis_seen[j] else 0) - 0.4 * (weed[j] if weed_seen[j] else 0), 0, 1)), 3),
        })
    return out


def probe_readings(model: FieldModel, probes: np.ndarray, rng: np.random.Generator) -> list[dict]:
    soil = model.soil
    rel = np.clip(1 - model.s["dr"][0, probes] / np.maximum(model.taw()[0, probes], 1e-6), 0, 1)
    theta = soil["wp"][probes] + (soil["fc"][probes] - soil["wp"][probes]) * rel + np.where(model.s["pond"][0, probes] > 1, 0.06, 0)
    theta = theta + rng.normal(0, 0.012, len(probes))
    return [{"i": int(i), "vwc": round(float(t), 4), "fc": round(float(soil["fc"][i]), 4), "wp": round(float(soil["wp"][i]), 4)}
            for i, t in zip(probes, theta)]
