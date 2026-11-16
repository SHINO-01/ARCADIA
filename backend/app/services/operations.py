"""Field operations: the season's work as campaigns over bed sections.

  seed       sowing window open for the section's crop, section fallow, soil workable
  fertilise  side-dress nitrogen once the crop is ~V6 (30 % of thermal time to flowering), non-legumes
  weed       UAV detected weeds in a planted section
  spray      UAV detected disease, plus a one-section buffer around each infected section
  harvest    crop ripe (the farm's phenology model), dry weather

Who does the work: the two physically simulated robots (Webots / headless twin) claim sections from
these queues through the dispatcher; the rest of the farm's fleet is modelled as `units` virtual
machines with realistic work rates (ha/h) and working hours. A 19 ha field with one real UGV could
never be sown inside a sowing window; a farm fleet sized to the field can. Field work stops on wet
days (the tramlines keep the soil workable longer than random traffic would).
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np

from app.agro.crops import CROPS, sow_months_for
from app.agro.model import FALLOW, FLOWERING, GRAIN_FILL, RIPE, SOWN, VEGETATIVE, FieldModel

KINDS = ("harvest", "spray", "weed", "fertilise", "seed")          # priority order
RATE_HA_H = {"seed": 2.0, "fertilise": 4.0, "weed": 1.5, "spray": 5.0, "harvest": 1.2}
N_RATE = {"maize": 180, "wheat": 140, "soybean": 0, "tomato": 170}  # kg N/ha per season
EVENT = {"seed": "seeded", "fertilise": "fertilised", "weed": "weeded", "spray": "sprayed", "harvest": "harvested"}
KIND_OF_EVENT = {v: k for k, v in EVENT.items()}
CLAIM_TTL_S = 1200.0


class Operations:
    def __init__(self, area_ha: np.ndarray, order: list[int], crop_keys: list[str], lat: float, units: int | None = None,
                 hours_per_day: float = 14.0):
        self.area = np.asarray(area_ha, float)
        self.order = list(order)                 # bed-by-bed sweep: how machines move through the field
        self.rank = {i: k for k, i in enumerate(self.order)}
        self.crop_keys = list(crop_keys)
        self.lat = lat
        total = float(self.area.sum())
        self.units = units or int(np.clip(math.ceil(total / 12.0), 1, 12))
        self.hours = hours_per_day
        self.pending: dict[str, list[int]] = {k: [] for k in KINDS}
        self.sidedressed = np.zeros(len(self.area), bool)
        self.carry = 0.0                         # unused machine-hours carried within the day
        self.done_today: dict[str, int] = {k: 0 for k in KINDS}
        self.season: dict[str, float] = {k: 0.0 for k in KINDS}  # ha done this season by kind
        self.workable = True
        self.harvest_ok = True

    # ------------------------------------------------------------------ planning (daily)
    def plan(self, model: FieldModel, today: date, obs: dict[int, dict], neighbours: list[list[int]], rain_mm: float) -> None:
        st = model.s["stage"][0]
        g = model.s["gdd"][0]
        c = model.c
        months = {k: sow_months_for(CROPS[k], self.lat) for k in set(self.crop_keys)}
        seed = [i for i in self.order if st[i] == FALLOW and today.month in months[self.crop_keys[i]]]
        fert = [i for i in self.order if st[i] == VEGETATIVE and not self.sidedressed[i] and not c["n_fixing"][i]
                and g[i] > 0.3 * c["gdd_flowering"][i] and N_RATE.get(self.crop_keys[i], 0) > 0]
        recent = {i: o for i, o in obs.items() if today.toordinal() - o["day"] <= 10}
        planted = lambda i: SOWN <= st[i] <= GRAIN_FILL  # noqa: E731
        weed = [i for i in self.order if i in recent and recent[i]["weeds"] and planted(i)]
        infected = {i for i, o in recent.items() if o["disease"] > 0 and planted(i)}
        buffer = {j for i in infected for j in neighbours[i] if planted(j)}
        spray = [i for i in self.order if i in infected | buffer]
        harvest = [i for i in self.order if st[i] == RIPE]
        self.pending = {"harvest": harvest, "spray": spray, "weed": weed, "fertilise": fert, "seed": seed}
        for i in np.flatnonzero(st == FALLOW):
            self.sidedressed[i] = False
        # wet days stop field work; harvest also needs a dry crop
        self.workable = rain_mm < 8.0
        self.harvest_ok = rain_mm < 2.0
        self.done_today = {k: 0 for k in KINDS}

    # ------------------------------------------------------------------ execution (continuous)
    def tick(self, day_fraction: float, claimed: set[int]) -> list[tuple[str, list[int]]]:
        """Virtual fleet progress over `day_fraction` of a day. Returns [(kind, sections)] done now."""
        if not self.workable:
            return []
        self.carry += self.units * self.hours * day_fraction  # machine-hours available
        out = []
        for kind in KINDS:
            if kind == "harvest" and not self.harvest_ok:
                continue
            todo = [i for i in self.pending[kind] if i not in claimed]
            done = []
            for i in todo:
                need = self.area[i] / RATE_HA_H[kind]
                if need > self.carry:
                    break
                self.carry -= need
                done.append(i)
            if done:
                self.pending[kind] = [i for i in self.pending[kind] if i not in set(done)]
                self.done_today[kind] += len(done)
                self.season[kind] += float(self.area[done].sum())
                out.append((kind, done))
            if self.carry < 1e-6:
                break
        if not any(self.pending.values()):
            self.carry = 0.0  # idle machines do not bank hours
        return out

    def complete(self, kind: str, idx: int) -> None:
        if idx in self.pending.get(kind, []):
            self.pending[kind].remove(idx)
        if kind == "fertilise":
            self.sidedressed[idx] = True
        self.season[kind] += float(self.area[idx])

    def summary(self) -> dict:
        return {"units": self.units, "hours_per_day": self.hours, "workable": self.workable,
                "pending": {k: len(v) for k, v in self.pending.items()},
                "pending_ha": {k: round(float(self.area[v].sum()), 2) if v else 0.0 for k, v in self.pending.items()},
                "done_today": dict(self.done_today), "season_ha": {k: round(v, 2) for k, v in self.season.items()}}

    def state(self) -> dict:
        return {"pending": self.pending, "sidedressed": self.sidedressed.tolist(), "carry": self.carry,
                "season": self.season, "units": self.units, "workable": self.workable}

    def load(self, st: dict) -> None:
        self.pending = {k: list(v) for k, v in st.get("pending", {}).items()} or self.pending
        sd = st.get("sidedressed")
        if sd and len(sd) == len(self.sidedressed):
            self.sidedressed = np.array(sd, bool)
        self.carry = float(st.get("carry", 0.0))
        self.season = {k: float(v) for k, v in st.get("season", self.season).items()}
        self.units = int(st.get("units", self.units))
        self.workable = bool(st.get("workable", True))
        self.harvest_ok = self.workable


__all__ = ["Operations", "KINDS", "EVENT", "KIND_OF_EVENT", "N_RATE", "RATE_HA_H"]
_ = FLOWERING
