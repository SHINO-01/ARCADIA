"""Monte Carlo forecasts: run the rest of the season many times under stochastic weather.

  * season forecast  - fork the live field state into E ensemble members and simulate each with its
                       own weather realisation until every planted section is ripe (harvested 3 days
                       later). Returns the farm-yield distribution (P10/P50/P90), per-section expected
                       yield, maturity dates and irrigation need.
  * weather outlook  - the next 7 days as daily medians and 10-90 % bands.
  * crop choice      - for each crop: sow every section at the next sowing window and simulate the
                       whole season from the farm's soils and climate -> yield, revenue, margin, risk.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from app.agro.crops import CROPS, sow_months_for
from app.agro.model import RIPE, SOWN, FieldModel
from app.agro.weather import WeatherGenerator


def _q(x, qs=(10, 50, 90)):
    return {f"p{q}": round(float(np.percentile(x, q)), 2) for q in qs}


def season(model: FieldModel, gen: WeatherGenerator, today: date, scenarios: list[dict] | None = None, E: int = 40,
           max_days: int = 260, seed: int = 0) -> dict:
    m = model.fork(E, seed)
    g = gen.fork(E, seed + 1)
    s = m.s
    planted = (s["stage"][0] >= SOWN) & (s["stage"][0] <= RIPE)
    if not planted.any():
        return {"planted_sections": 0}
    done = np.zeros((E, m.n), bool)
    final = np.zeros((E, m.n))
    mature_day = np.full((E, m.n), np.nan)
    irr0 = s["irrigated_mm"].copy()
    d = today
    for k in range(max_days):
        d = d + timedelta(days=1)
        m.step(g.step(d, scenarios))
        ripe = (s["stage"] >= RIPE) & ~done & planted[None, :]
        if ripe.any():
            # harvested 3 days after maturity on average: take the potential then, minus a small loss
            final = np.where(ripe, m.yield_potential() * 0.985, final)
            mature_day = np.where(ripe, k + 1, mature_day)
            done |= ripe
        if done[:, planted].all():
            break
    area = m.area
    farm_t = (final * area).sum(axis=1)
    md = np.nanmedian(mature_day[:, planted], axis=0)
    price = (m.c["price_per_t"] * area)
    revenue = (final * price).sum(axis=1)
    return {
        "members": E, "planted_sections": int(planted.sum()), "planted_ha": round(float(area[planted].sum()), 2),
        "yield_t": _q(farm_t), "yield_t_ha": _q(farm_t / max(area[planted].sum(), 1e-6)), "revenue_usd": _q(revenue),
        "maturity_date": {k: (today + timedelta(days=int(v))).isoformat() for k, v in _q(np.nanmedian(mature_day[:, planted], axis=1)).items()
                          if np.isfinite(v)},
        "irrigation_mm": _q(((s["irrigated_mm"] - irr0)[:, planted] * area[planted]).sum(axis=1) / max(area[planted].sum(), 1e-6)),
        "section_yield_t_ha": np.round(final.mean(axis=0), 2).tolist(),
        "section_maturity_day": [None if not np.isfinite(x) else int(x) for x in md.tolist()] if len(md) else [],
        "spread_cv": round(float(farm_t.std() / max(farm_t.mean(), 1e-6)), 3),
    }


def weather_outlook(gen: WeatherGenerator, today: date, scenarios: list[dict] | None = None, days: int = 7, E: int = 50,
                    seed: int = 0) -> list[dict]:
    g = gen.fork(E, seed)
    out = []
    d = today
    for _ in range(days):
        d = d + timedelta(days=1)
        w = g.step(d, scenarios)
        out.append({"date": d.isoformat(), "tmax": _q(w.tmax), "tmin": _q(w.tmin), "rain_mm": _q(w.rain),
                    "rain_prob": round(float((w.rain >= 1.0).mean()), 2), "et0": _q(w.et0), "wind": _q(w.wind)})
    return out


def next_sowing(crop_key: str, lat: float, today: date) -> date:
    months = sow_months_for(CROPS[crop_key], lat)
    for k in range(0, 400):
        d = today + timedelta(days=k)
        if d.month in months:
            return d
    return today


def crop_choice(base: FieldModel, gen_params: dict, today: date, E: int = 16, seed: int = 0) -> list[dict]:
    """Simulate a full season for each catalogue crop sown at its next window on this farm."""
    out = []
    lat = float(gen_params["lat"])
    for key, crop in CROPS.items():
        m = base.fork(E, seed)
        m.set_crops([key] * m.n)
        m.s["stage"][:] = 0
        idx = np.arange(m.n)
        m.sow(idx)
        n_rate = {"maize": 180, "wheat": 140, "soybean": 0, "tomato": 170}.get(key, 120)
        m.fertilise(idx, 0.4 * n_rate)  # standard management: starter N, side-dress at ~V6, weeding
        side = False
        g = WeatherGenerator(gen_params, E, seed + 7)
        sow = next_sowing(key, lat, today)
        d = sow
        final = np.zeros((E, m.n))
        done = np.zeros((E, m.n), bool)
        days = 0
        for days in range(1, 320):
            d = d + timedelta(days=1)
            m.step(g.step(d))
            frac = float(np.mean(m.s["gdd"][0] / m.c["gdd_flowering"]))
            if not side and frac > 0.3:
                m.fertilise(idx, 0.6 * n_rate)
                side = True
            if days % 20 == 0 and frac < 1.0:
                m.weed_control(idx)
            ripe = (m.s["stage"] >= RIPE) & ~done
            final = np.where(ripe, m.yield_potential() * 0.985, final)
            done |= ripe
            if done.all():
                break
        area = m.area.sum()
        tha = (final * m.area).sum(axis=1) / area
        revenue = tha * crop.price_per_t
        margin = revenue - crop.cost_per_ha
        out.append({"crop": key, "name": crop.name, "sow_date": sow.isoformat(), "season_days": days,
                    "yield_t_ha": _q(tha), "margin_usd_ha": _q(margin), "revenue_usd_ha": _q(revenue),
                    "risk_cv": round(float(tha.std() / max(tha.mean(), 1e-6)), 3),
                    "failure_prob": round(float((tha < 0.3 * max(np.median(tha), 1e-6)).mean()), 3),
                    "water_stress_days": round(float(m.s["water_stress_days"].mean()), 1),
                    "irrigation_mm": round(float(m.s["irrigated_mm"].mean()), 0)})
    best = max(out, key=lambda r: r["margin_usd_ha"]["p50"] - 0.5 * (r["margin_usd_ha"]["p50"] - r["margin_usd_ha"]["p10"]))
    for r in out:
        r["recommended"] = r is best
    return sorted(out, key=lambda r: -r["margin_usd_ha"]["p50"])


__all__ = ["season", "weather_outlook", "crop_choice", "next_sowing"]
