"""Statistical anomaly detection over weather, water and crop observations.

Methods (all on data the farm can measure - weather station, probes, UAV, flow meter):

  heatwave        Tmax z-score against the fitted daily climatology; >= 3 consecutive days above
                  the 95th percentile (z > 1.645) and 30 degC
  frost           Tmin below the crop's damage threshold at a sensitive stage
  drought         SPI-30: 30-day rainfall through the month's fitted gamma CDF -> standard normal
                  (McKee 1993); <= -1 moderate, <= -1.5 severe, <= -2 extreme
  extreme rain    daily total above the station's 99th percentile of wet days
  waterlogging    probes at saturation for 2+ days or ponding seen by the UAV
  water stress    thermal CWSI > 0.5 on more than 20 % of recently observed area
  crop hotspot    NDVI spatial outliers: robust z = (x - median of neighbours) / (1.4826 MAD) < -2.5,
                  clustered, with the likely cause inferred from co-located evidence
  disease         a connected cluster of 3+ sections with detected infection
  irrigation      one-sided CUSUM on (water demanded - water delivered) from the flow meter
"""
from __future__ import annotations

from collections import deque
from math import sqrt

import numpy as np
from scipy import stats

from app.agro.climate import harmonic
from app.agro.weather import Day

SEV_ORDER = {"info": 0, "warning": 1, "critical": 2}


class AnomalyDetector:
    def __init__(self, climate: dict, section_ids: list[str], neighbours: list[list[int]]):
        self.clim = climate["clim"]
        self.ids = section_ids
        self.nb = neighbours
        self.rain30: deque[float] = deque(maxlen=30)
        self.heat_run = 0
        self.cusum = 0.0
        self.probe_sat: dict[int, int] = {}
        self.active: dict[str, dict] = {}

    def spi30(self, month: int) -> float | None:
        if len(self.rain30) < 30:
            return None
        shape, scale, p0 = self.clim["spi30"][month - 1]
        x = sum(self.rain30)
        p = p0 + (1 - p0) * (stats.gamma.cdf(x, shape, scale=scale) if x > 0.1 else 0.0)
        return float(stats.norm.ppf(np.clip(p, 1e-4, 1 - 1e-4)))

    def update(self, day: Day, *, crop_sensitive: bool, frost_kill: float, obs: dict, probes: list[dict],
               irrigation_demand_mm: float, irrigation_delivered_mm: float) -> tuple[list[dict], list[str]]:
        """Returns (new or updated anomalies, keys that cleared)."""
        found: dict[str, dict] = {}
        tmax, tmin, rain = float(day.tmax[0]), float(day.tmin[0]), float(day.rain[0])
        doy = day.doy
        mu = float(harmonic(self.clim["tmax_mean"], doy)[0])
        sd = sqrt(max(0.5, float(harmonic(self.clim["tmax_sd"], doy)[0])))
        z = (tmax - mu) / sd
        self.heat_run = self.heat_run + 1 if (z > 1.645 and tmax > 30) else 0
        if self.heat_run >= 3:
            found["heatwave"] = _a("heatwave", "critical" if tmax > 38 else "warning", f"Heatwave: day {self.heat_run}, Tmax {tmax:.1f} degC",
                                   f"Tmax is {z:.1f} SD above the {mu:.1f} degC climatological mean for this date.",
                                   method="z-score vs fitted daily climatology", value=round(z, 2), threshold=1.645,
                                   action="Irrigate ahead of demand at night; avoid field operations at midday; watch pollination.")
        if tmin < frost_kill and crop_sensitive:
            found["frost"] = _a("frost", "critical", f"Frost: Tmin {tmin:.1f} degC", "Below the crop's damage threshold at a sensitive stage.",
                                method="threshold on crop frost tolerance", value=round(tmin, 1), threshold=frost_kill,
                                action="Expect canopy damage; scout affected beds after sunrise; reassess the yield forecast.")
        self.rain30.append(rain)
        spi = self.spi30(day.date.month)
        if spi is not None and spi <= -1.0:
            sev = "critical" if spi <= -1.5 else "warning"
            found["drought"] = _a("drought", sev, f"Meteorological drought (SPI-30 {spi:.2f})",
                                  f"{sum(self.rain30):.0f} mm in 30 days is unusually dry for the month.",
                                  method="Standardised Precipitation Index (gamma fit, 30 d)", value=round(spi, 2), threshold=-1.0,
                                  action="Prioritise irrigation to beds at flowering; lower the depletion trigger.")
        if rain > self.clim["rain_p99"]:
            found["extreme_rain"] = _a("extreme_rain", "warning", f"Extreme rainfall: {rain:.0f} mm",
                                       f"Above the station's 99th percentile ({self.clim['rain_p99']:.0f} mm).",
                                       method="percentile exceedance", value=round(rain, 1), threshold=round(self.clim["rain_p99"], 1),
                                       action="Keep machines on the tramlines; check hollows for ponding.")
        # probes: saturation persistence
        sat_secs = []
        for p in probes:
            i = p["i"]
            sat = p["vwc"] > p["fc"] + 0.035
            self.probe_sat[i] = self.probe_sat.get(i, 0) + 1 if sat else 0
            if self.probe_sat[i] >= 2:
                sat_secs.append(i)
        ponded = [i for i, o in obs.items() if o.get("ponding") and o["age_days"] <= 3]
        wl = sorted(set(sat_secs) | set(ponded))
        if wl:
            found["waterlogging"] = _a("waterlogging", "warning", f"Waterlogging in {len(wl)} section(s)",
                                       "Soil at saturation (probes) or standing water (UAV).", sections=[self.ids[i] for i in wl],
                                       method="probe saturation run-length + UAV ponding", value=len(wl), threshold=1,
                                       action="Suspend irrigation there; keep traffic off; consider drainage in the next layout.")
        recent = {i: o for i, o in obs.items() if o["age_days"] <= 7}
        if len(recent) >= 10:
            stressed = [i for i, o in recent.items() if o["cwsi"] > 0.5]
            if len(stressed) / len(recent) > 0.2:
                found["water_stress"] = _a("water_stress", "warning", f"Water stress on {100 * len(stressed) / len(recent):.0f}% of scouted area",
                                           "Thermal imagery shows closed stomata (CWSI > 0.5).", sections=[self.ids[i] for i in stressed[:40]],
                                           method="CWSI share of observed sections", value=round(len(stressed) / len(recent), 2), threshold=0.2,
                                           action="Irrigate the listed beds; check the irrigation system pressure.")
            hot = self._ndvi_outliers(recent)
            if hot:
                cause = self._cause(hot, recent)
                found["crop_hotspot"] = _a("crop_hotspot", "warning", f"Crop stress hotspot ({len(hot)} sections): likely {cause}",
                                           "NDVI well below neighbouring sections.", sections=[self.ids[i] for i in hot],
                                           method="robust spatial z-score (median/MAD of neighbours)", value=len(hot), threshold=-2.5,
                                           action={"water stress": "Irrigate", "disease": "Scout and spray", "waterlogging": "Improve drainage",
                                                   "weeds": "Weed the beds", "nitrogen deficiency": "Side-dress nitrogen",
                                                   "salinity": "Leach salts / tolerant crop"}.get(cause, "Scout the area"))
            infected = {i for i, o in recent.items() if o["disease"] > 0}
            clusters = self._clusters(infected)
            big = [c for c in clusters if len(c) >= 3]
            if big:
                secs = sorted(set().union(*big))
                found["disease"] = _a("disease", "critical" if len(secs) > 10 else "warning", f"Disease outbreak: {len(secs)} sections in {len(big)} cluster(s)",
                                      "Connected sections with detected infection.", sections=[self.ids[i] for i in secs],
                                      method="connected-component clustering of detections", value=len(secs), threshold=3,
                                      action="Spray the cluster and a one-section buffer around it.")
        # irrigation system CUSUM (k = 1 mm slack, h = 6 mm)
        self.cusum = max(0.0, self.cusum + (irrigation_demand_mm - irrigation_delivered_mm) - 1.0)
        if self.cusum > 6.0:
            found["irrigation"] = _a("irrigation", "critical", "Irrigation shortfall",
                                     f"Delivered water is persistently below demand (CUSUM {self.cusum:.1f} mm).",
                                     method="one-sided CUSUM on demand - delivery", value=round(self.cusum, 1), threshold=6.0,
                                     action="Inspect pumps, filters and mainline; switch to manual irrigation of critical beds.")
        cleared = [k for k in self.active if k not in found]
        for k in cleared:
            self.active.pop(k)
        changed = []
        for k, a in found.items():
            prev = self.active.get(k)
            a["since"] = prev["since"] if prev else day.date.isoformat()
            a["updated"] = day.date.isoformat()
            self.active[k] = a
            if prev is None or prev["title"] != a["title"]:
                changed.append(a)
        return changed, cleared

    def _ndvi_outliers(self, recent: dict) -> list[int]:
        keys = list(recent)
        vals = {i: recent[i]["ndvi"] for i in keys}
        res = {}
        for i in keys:
            nbv = [vals[j] for j in self.nb[i] if j in vals]
            if len(nbv) >= 2:
                res[i] = vals[i] - float(np.median(nbv))
        if len(res) < 8:
            return []
        r = np.array(list(res.values()))
        mad = 1.4826 * float(np.median(np.abs(r - np.median(r)))) + 0.01
        return [i for i, v in res.items() if v / mad < -2.5 and vals[i] < 0.75]

    def _cause(self, secs: list[int], recent: dict) -> str:
        o = [recent[i] for i in secs]
        votes = {"water stress": sum(x["cwsi"] > 0.5 for x in o), "disease": sum(x["disease"] > 0 for x in o),
                 "waterlogging": sum(x["ponding"] for x in o), "weeds": sum(x["weeds"] for x in o)}
        best = max(votes, key=votes.get)
        return best if votes[best] >= max(1, len(o) // 3) else "nitrogen deficiency"

    def _clusters(self, nodes: set[int]) -> list[set[int]]:
        out, seen = [], set()
        for n in nodes:
            if n in seen:
                continue
            comp, stack = set(), [n]
            while stack:
                u = stack.pop()
                if u in seen:
                    continue
                seen.add(u)
                comp.add(u)
                stack += [v for v in self.nb[u] if v in nodes and v not in seen]
            out.append(comp)
        return out


def _a(kind, severity, title, detail, *, method, value, threshold, action, sections=None) -> dict:
    return {"key": kind, "kind": kind, "severity": severity, "title": title, "detail": detail, "method": method,
            "value": value, "threshold": threshold, "action": action, "sections": sections or []}
