"""Daily field model, vectorised over (ensemble E, sections n).

Per section and day:

  water     FAO-56 root-zone depletion balance: rain - SCS-CN runoff + run-on (runoff routed to
            wet hollows by terrain wetness) + irrigation - ETc (Kc from canopy cover, Ks water-stress
            coefficient with the p adjustment for evaporative demand); excess drains up to Ksat,
            the rest ponds -> waterlogging days
  crop      thermal time (degC.d) drives emergence -> flowering -> grain fill -> maturity;
            LAI follows a logistic potential scaled by stress, senesces during grain fill;
            biomass = RUE x intercepted PAR (Beer's law) x min(water, N, O2) x temperature x
            salinity (Maas-Hoffman) x weed competition (Cousens) ; yield = biomass x HI, with HI
            penalties for heat, frost and drought around flowering (Ky-scaled)
  nitrogen  mineralisation from organic matter, leaching with drainage, uptake to the critical
            N dilution curve; the N nutrition index limits growth (legumes fix)
  weeds     rain-triggered emergence from the seedbank, logistic growth shaded by the crop
  disease   infection on warm wet days, spread from neighbouring sections, protective sprays
  hazards   frost, heat, hail, waterlogging, post-maturity field losses (shattering, rot)

Operations (sow, fertilise, weed, spray, harvest) are applied to section masks.
"""
from __future__ import annotations

import base64
import io

import numpy as np

from app.agro.crops import CROPS, Crop
from app.agro.weather import Day

FALLOW, SOWN, VEGETATIVE, FLOWERING, GRAIN_FILL, RIPE, STUBBLE = range(7)
STAGE_NAMES = ["fallow", "sown", "vegetative", "flowering", "grain_fill", "ripe", "harvested"]
STATE_FIELDS = ("stage", "gdd", "das", "lai", "lai_fl", "biomass", "hi_f", "zr", "dr", "pond", "wl_days", "n_soil",
                "n_up", "weed", "seedbank", "disease", "protect", "ripe_days", "loss", "stubble_days", "ks", "height",
                "frost_dmg", "irrigated_mm", "rain_mm", "etc_mm", "water_stress_days")


class FieldModel:
    def __init__(self, soil: dict, area_ha: np.ndarray, crop_keys: list[str], neighbours=None, ensemble: int = 1,
                 seed: int = 0):
        """soil: per-section arrays from SoilField.sec (fc, wp, ksat, cn, ec, twi_z, n0, n_min_rate)."""
        self.n = len(area_ha)
        self.E = ensemble
        self.rng = np.random.default_rng(seed + 77)
        self.area = np.asarray(area_ha, float)
        self.soil = {k: np.asarray(v, float) for k, v in soil.items()}
        self.set_crops(crop_keys)
        self.W = neighbours  # scipy.sparse (n x n), row-normalised adjacency
        w = np.exp(1.2 * np.clip(self.soil["twi_z"], -3, 3)) * self.area
        self.runon_share = w / w.sum()
        self.irrigation = {"enabled": True, "mad": 0.5, "capacity_mm": 8.0, "efficiency": 0.85, "failed": False}
        E, n = ensemble, self.n
        z = lambda: np.zeros((E, n))  # noqa: E731
        self.s = {k: z() for k in STATE_FIELDS}
        self.s["stage"] = np.zeros((E, n), dtype=int)
        self.s["hi_f"] = np.ones((E, n))
        self.s["zr"] = np.full((E, n), 0.1)
        self.s["dr"] = np.tile(0.35 * 1000 * (self.soil["fc"] - self.soil["wp"]) * 0.3, (E, 1))
        self.s["n_soil"] = np.tile(self.soil["n0"], (E, 1))
        self.s["seedbank"] = np.tile(np.clip(0.25 + 0.15 * self.rng.standard_normal(n), 0.05, 0.6), (E, 1))
        self.s["ks"] = np.ones((E, n))

    # ------------------------------------------------------------------ crops
    def set_crops(self, crop_keys: list[str]) -> None:
        self.crop_keys = list(crop_keys)
        crops = [CROPS[k] for k in crop_keys]
        self.c = {f: np.array([getattr(c, f) for c in crops], dtype=float) for f in (
            "t_base", "t_cut", "gdd_emergence", "gdd_flowering", "gdd_maturity", "t_opt_lo", "t_opt_hi", "t_max_g",
            "lai_max", "k_ext", "rue", "hi", "dm_fraction", "height_max", "kc_ini", "kc_mid", "kc_end", "root_min",
            "root_max", "p", "ky", "n_a", "n_b", "frost_kill", "heat_crit", "ec_threshold", "ec_slope",
            "waterlog_tolerance", "price_per_t", "cost_per_ha")}
        self.c["n_fixing"] = np.array([c.n_fixing for c in crops])
        self.f_sal = np.clip(1 - self.c["ec_slope"] * (self.soil["ec"] - self.c["ec_threshold"]) / 100, 0.05, 1.0)

    def crop_at(self, i: int) -> Crop:
        return CROPS[self.crop_keys[i]]

    # ------------------------------------------------------------------ helpers
    def _temp_factor(self, t):
        c = self.c
        up = np.clip((t - c["t_base"]) / np.maximum(c["t_opt_lo"] - c["t_base"], 1), 0, 1)
        down = np.clip((c["t_max_g"] - t) / np.maximum(c["t_max_g"] - c["t_opt_hi"], 1), 0, 1)
        return np.minimum(up, down)

    def _lai_potential(self, gdd):
        fv = gdd / self.c["gdd_flowering"]
        return self.c["lai_max"] / (1 + np.exp(-10 * (fv - 0.45)))

    def taw(self):
        return 1000 * (self.soil["fc"] - self.soil["wp"]) * self.s["zr"]

    # ------------------------------------------------------------------ daily step
    def step(self, day: Day) -> dict:
        s, c, soil = self.s, self.c, self.soil
        tmax, tmin = day.tmax[:, None], day.tmin[:, None]
        tmean = (tmax + tmin) / 2
        rain, et0, rs = day.rain[:, None], day.et0[:, None], day.rs[:, None]
        stage = s["stage"]
        crop_on = (stage >= SOWN) & (stage <= RIPE)
        developing = crop_on & (stage < RIPE)

        # ---- phenology
        dtt = np.clip(np.minimum(tmean, c["t_cut"]) - c["t_base"], 0, None)
        gdd_prev = s["gdd"].copy()
        s["gdd"] = np.where(developing, s["gdd"] + dtt, s["gdd"])
        s["das"] = np.where(crop_on, s["das"] + 1, s["das"])
        g = s["gdd"]
        new = stage.copy()
        new = np.where((stage == SOWN) & (g >= c["gdd_emergence"]), VEGETATIVE, new)
        new = np.where((stage == VEGETATIVE) & (g >= c["gdd_flowering"]), FLOWERING, new)
        flower_end = c["gdd_flowering"] + 0.12 * (c["gdd_maturity"] - c["gdd_flowering"])
        new = np.where((stage == FLOWERING) & (g >= flower_end), GRAIN_FILL, new)
        new = np.where((stage == GRAIN_FILL) & (g >= c["gdd_maturity"]), RIPE, new)
        s["lai_fl"] = np.where((new == FLOWERING) & (stage == VEGETATIVE), s["lai"], s["lai_fl"])
        stage = s["stage"] = new
        s["stubble_days"] = np.where(stage == STUBBLE, s["stubble_days"] + 1, 0)
        stage = s["stage"] = np.where((stage == STUBBLE) & (s["stubble_days"] > 14), FALLOW, stage)
        crop_on = (stage >= SOWN) & (stage <= RIPE)
        canopy = (stage >= VEGETATIVE) & (stage <= RIPE)

        # ---- roots and crop coefficient
        s["zr"] = np.where(crop_on, c["root_min"] + (c["root_max"] - c["root_min"]) * np.clip(g / c["gdd_flowering"], 0, 1), 0.1)
        green = s["lai"] * (1 - s["disease"])
        cover = np.where(canopy, 1 - np.exp(-c["k_ext"] * green), 0.0)
        cover_ref = 1 - np.exp(-c["k_ext"] * c["lai_max"])
        kc = np.where(crop_on, np.maximum(c["kc_ini"], c["kc_ini"] + (c["kc_mid"] + 0.05 - c["kc_ini"]) * cover / cover_ref), 0.0)
        kc = np.where(stage == RIPE, np.minimum(kc, c["kc_end"]), kc)
        bare = np.where(rain > 2, 0.35, 0.15)
        kc = np.where(crop_on, kc, bare) + 0.8 * s["weed"] * (1 - cover)
        kc = np.clip(kc, 0, 1.4)

        # ---- water balance
        TAW = self.taw()
        p_adj = np.clip(c["p"] + 0.04 * (5 - kc * et0), 0.1, 0.8)
        RAW = p_adj * TAW
        irr = np.zeros_like(TAW)
        ip = self.irrigation
        if ip["enabled"] and not ip["failed"]:
            need = crop_on & (stage <= GRAIN_FILL) & (s["dr"] > ip["mad"] * TAW)
            irr = np.where(need, np.minimum(s["dr"], ip["capacity_mm"]), 0.0)
        S = 25400 / soil["cn"] - 254
        Ia = 0.2 * S
        Q = np.where(rain > Ia, (rain - Ia) ** 2 / (rain + 0.8 * S), 0.0)
        runoff_m3 = (Q * self.area).sum(axis=1, keepdims=True)  # mm*ha
        runon = 0.6 * runoff_m3 * self.runon_share / self.area
        infil = rain - Q + runon + irr
        ks = np.where(s["dr"] > RAW, np.clip((TAW - s["dr"]) / ((1 - p_adj) * TAW + 1e-6), 0, 1), 1.0)
        ks = np.where(crop_on, ks, 1.0)
        etc = ks * kc * et0
        dr = s["dr"] - infil + etc
        excess = np.clip(-dr, 0, None)
        dr = np.clip(dr, 0, TAW)
        drain_cap = soil["ksat"] * 24 * 0.5
        dp = np.minimum(excess, drain_cap)
        s["pond"] = s["pond"] + excess - dp
        s["pond"] = s["pond"] - np.minimum(s["pond"], 0.6 * drain_cap + 0.8 * et0)
        saturated = (s["pond"] > 2) | ((dr < 0.05 * TAW) & (excess > 0.5 * drain_cap))
        s["wl_days"] = np.where(saturated, s["wl_days"] + 1, np.maximum(s["wl_days"] - 1, 0))
        s["dr"] = dr
        s["ks"] = ks
        s["irrigated_mm"] += irr / ip["efficiency"] if ip["enabled"] else 0.0
        s["rain_mm"] += rain
        s["etc_mm"] += etc
        s["water_stress_days"] += crop_on & (ks < 0.7)

        # ---- nitrogen
        theta_rel = 1 - dr / np.maximum(TAW, 1e-6)
        f_tmin = np.clip((tmean - 2) / 25, 0, 1.3)
        leach = s["n_soil"] * np.clip(dp / (TAW + 80), 0, 0.5)
        s["n_soil"] = np.clip(s["n_soil"] + soil["n_min_rate"] * f_tmin * np.clip(theta_rel + 0.2, 0, 1) - leach, 0, 600)

        # ---- stresses
        fT = np.where(canopy, self._temp_factor(tmean), 0.0)
        tol = c["waterlog_tolerance"]
        f_wl = 1 - np.clip((s["wl_days"] - tol) / 6, 0, 0.85)
        frost = (tmin < c["frost_kill"]) & (stage >= VEGETATIVE) & (stage <= GRAIN_FILL)
        dmg = np.where(frost, np.clip((c["frost_kill"] - tmin) / 6, 0, 1) * 0.7, 0.0)
        s["lai"] *= 1 - dmg
        s["frost_dmg"] = np.maximum(s["frost_dmg"], dmg)
        s["hi_f"] *= np.where(frost & (stage >= FLOWERING), 1 - 0.5 * dmg, 1)
        heat = (stage == FLOWERING) & (tmax > c["heat_crit"])
        s["hi_f"] *= np.where(heat, 1 - 0.05 * (tmax - c["heat_crit"]), 1)
        s["hi_f"] *= np.where(stage == FLOWERING, 1 - 0.04 * c["ky"] * (1 - ks), 1)
        s["hi_f"] = np.clip(s["hi_f"], 0.05, 1)
        D = 120 * s["weed"]
        f_weed = 1 - (0.9 * D) / (1 + 0.9 * D / 55) / 100
        W0 = s["biomass"]
        nc_pct = c["n_a"] * np.maximum(W0, 1.0) ** (-c["n_b"])
        n_crit = 10 * nc_pct * W0
        nni = np.where(W0 > 0.5, s["n_up"] / np.maximum(n_crit, 1e-6), 1.0)
        f_N = np.where(c["n_fixing"], 1.0, np.clip(nni, 0.3, 1.0))

        # ---- growth
        par = 0.48 * rs
        growing = (stage >= VEGETATIVE) & (stage <= GRAIN_FILL)
        dW = np.where(growing, c["rue"] * par * cover * 0.01 * fT * np.minimum(np.minimum(ks, f_N), f_wl) * self.f_sal * f_weed, 0.0)
        s["biomass"] += dW
        W1 = s["biomass"]
        demand = np.clip(10 * c["n_a"] * np.maximum(W1, 1.0) ** (-c["n_b"]) * W1 - s["n_up"], 0, None)
        uptake = np.minimum(demand, np.minimum(0.8 * s["n_soil"] * np.clip(s["zr"], 0.2, 1), 6.0))
        uptake = np.where(c["n_fixing"], demand, uptake)
        s["n_up"] += np.where(growing, uptake, 0)
        s["n_soil"] -= np.where(growing & ~c["n_fixing"], uptake, np.where(growing, 0.3 * np.minimum(uptake, s["n_soil"]), 0))
        s["n_soil"] = np.clip(s["n_soil"], 0, None)

        veg = stage == VEGETATIVE
        dlai_pot = np.clip(self._lai_potential(g) - self._lai_potential(gdd_prev), 0, None)
        stress = np.minimum(np.minimum(ks, f_N), f_wl) * np.clip(fT + 0.2, 0, 1)
        s["lai"] = np.where(veg, s["lai"] + dlai_pot * stress, s["lai"])
        s["lai"] = np.where((stage == SOWN) & (g >= 0.6 * c["gdd_emergence"]), 0.05, s["lai"])
        fill = stage == GRAIN_FILL
        span = np.maximum(c["gdd_maturity"] - flower_end, 1)
        sen = np.maximum(s["lai_fl"], s["lai"]) * dtt / span * (1 + 1.5 * (1 - ks))
        s["lai"] = np.where(fill, np.maximum(s["lai"] - 0.8 * sen, 0.2), s["lai"])
        s["lai"] = np.where(stage == RIPE, s["lai"] * 0.9, s["lai"])
        s["lai"] = np.where(crop_on, np.clip(s["lai"], 0, c["lai_max"] * 1.1), 0)
        rel = np.clip(s["lai"] / c["lai_max"], 0, 1) ** 0.7
        s["height"] = np.where(veg | (stage == SOWN), c["height_max"] * rel,
                               np.where(crop_on, np.maximum(s["height"], 0.0), np.where(stage == STUBBLE, 0.15, 0.0)))

        # ---- weeds
        emerge = (rain > 5) & (tmean > 8)
        fTw = np.clip((tmean - 5) / 18, 0, 1)
        s["weed"] = s["weed"] + np.where(emerge, 0.02 * s["seedbank"] * (1 - cover), 0.0)
        s["weed"] = s["weed"] + 0.07 * fTw * s["weed"] * (1 - s["weed"] - 0.6 * cover)
        s["weed"] = np.clip(s["weed"], 0, 1)
        s["seedbank"] = np.clip(s["seedbank"] + 0.002 * s["weed"] - 0.0005 * s["seedbank"], 0, 1)

        # ---- disease
        wet_leaf = (rain > 1) | (day.rh[:, None] > 85)
        fav = wet_leaf & (tmean > 14) & (tmean < 29)
        fav_f = np.where(fav, 1.0, 0.25)
        if self.W is not None:
            nb = (self.W @ s["disease"].T).T
        else:
            nb = np.zeros_like(s["disease"])
        p_inf = np.where(canopy & (s["protect"] <= 0), 0.004 * fav * cover + 0.18 * nb * fav, 0.0)
        infect = (s["disease"] < 1e-6) & (self.rng.random(s["disease"].shape) < p_inf)
        s["disease"] = np.where(infect, 0.01, s["disease"])
        s["disease"] = np.where(canopy & (s["disease"] > 0),
                                s["disease"] + 0.14 * fav_f * s["disease"] * (1 - s["disease"]) * np.where(s["protect"] > 0, 0.2, 1),
                                np.where(canopy, s["disease"], 0.0))
        s["disease"] = np.clip(s["disease"], 0, 1)
        s["protect"] = np.maximum(s["protect"] - 1, 0)

        # ---- hail and post-maturity losses
        hail = day.hail[:, None] & (self.rng.random(s["lai"].shape) < 0.6) & canopy
        s["biomass"] *= np.where(hail, 0.85, 1)
        s["lai"] *= np.where(hail, 0.7, 1)
        s["hi_f"] *= np.where(hail & (stage >= FLOWERING), 0.9, 1)
        s["ripe_days"] = np.where(stage == RIPE, s["ripe_days"] + 1, 0)
        late = np.clip(s["ripe_days"] - 7, 0, None)
        s["loss"] = np.where(stage == RIPE, np.clip(s["loss"] + 0.01 * (late > 0) + 0.008 * (rain > 10), 0, 0.6), s["loss"])

        return self.daily_summary(day, irr)

    # ------------------------------------------------------------------ outputs
    def yield_potential(self) -> np.ndarray:
        """Expected fresh marketable yield (t/ha) if harvested at maturity from the current state."""
        s, c = self.s, self.c
        hi = np.minimum(c["hi"] * s["hi_f"], 0.62)
        return s["biomass"] * hi / c["dm_fraction"] * (1 - s["loss"])

    def ndvi(self) -> np.ndarray:
        s, c = self.s, self.c
        green = s["lai"] * (1 - s["disease"])
        cover = 1 - np.exp(-c["k_ext"] * green)
        return np.clip(0.86 - (0.86 - 0.17) * np.exp(-0.55 * green) + 0.22 * s["weed"] * (1 - cover)
                       - 0.06 * (s["stage"] == RIPE), 0.05, 0.92)

    def moisture(self) -> np.ndarray:
        """Relative root-zone water (1 = field capacity, 0 = wilting point; >1 when ponded)."""
        TAW = np.maximum(self.taw(), 1e-6)
        return np.clip(1 - self.s["dr"] / TAW + self.s["pond"] / 50, 0, 1.5)

    def health(self) -> np.ndarray:
        s, c = self.s, self.c
        f_wl = 1 - np.clip((s["wl_days"] - c["waterlog_tolerance"]) / 6, 0, 0.85)
        D = 120 * s["weed"]
        f_weed = 1 - (0.9 * D) / (1 + 0.9 * D / 55) / 100
        return np.clip(np.minimum.reduce([s["ks"], f_wl, f_weed, 1 - s["disease"], self.f_sal * np.ones_like(s["ks"])]), 0, 1)

    def daily_summary(self, day: Day, irr: np.ndarray) -> dict:
        s = self.s
        a = self.area / self.area.sum()
        crop = (s["stage"] >= SOWN) & (s["stage"] <= RIPE)
        return {
            "date": day.date.isoformat(),
            "lai": float((s["lai"][0] * a).sum()), "biomass_t_ha": float((s["biomass"][0] * a).sum()),
            "yield_pot_t": float((self.yield_potential()[0] * self.area).sum()),
            "moisture": float((self.moisture()[0] * a).sum()), "ks": float((s["ks"][0] * a).sum()),
            "irrigation_m3": float((irr[0] / self.irrigation["efficiency"] * self.area * 10).sum()) if self.irrigation["enabled"] else 0.0,
            "weed": float((s["weed"][0] * a).sum()), "disease": float((s["disease"][0] * a).sum()),
            "waterlogged_pct": float(100 * ((s["wl_days"][0] > 0) * a).sum()), "n_soil": float((s["n_soil"][0] * a).sum()),
            "crop_pct": float(100 * (crop[0] * a).sum()), "ndvi": float((self.ndvi()[0] * a).sum()),
            "stage_counts": {STAGE_NAMES[k]: int((s["stage"][0] == k).sum()) for k in range(7)},
        }

    # ------------------------------------------------------------------ operations
    def _m(self, mask) -> np.ndarray:
        m = np.zeros(self.n, bool)
        m[np.asarray(mask)] = True
        return np.broadcast_to(m, (self.E, self.n))

    def sow(self, idx) -> None:
        m = self._m(idx)
        s = self.s
        for k in ("gdd", "das", "lai", "lai_fl", "biomass", "n_up", "weed", "disease", "protect", "ripe_days", "loss",
                  "frost_dmg", "height", "stubble_days", "water_stress_days"):
            s[k] = np.where(m, 0.0, s[k])
        s["stage"] = np.where(m, SOWN, s["stage"])
        s["hi_f"] = np.where(m, 1.0, s["hi_f"])
        s["seedbank"] = np.where(m, s["seedbank"] * 0.8, s["seedbank"])

    def fertilise(self, idx, kg_n: float = 60.0) -> None:
        m = self._m(idx)
        self.s["n_soil"] = np.where(m, self.s["n_soil"] + 0.85 * kg_n, self.s["n_soil"])

    def weed_control(self, idx) -> None:
        m = self._m(idx)
        self.s["weed"] = np.where(m, self.s["weed"] * 0.1, self.s["weed"])
        self.s["seedbank"] = np.where(m, self.s["seedbank"] * 0.9, self.s["seedbank"])

    def spray(self, idx) -> None:
        m = self._m(idx)
        self.s["disease"] = np.where(m, self.s["disease"] * 0.35, self.s["disease"])
        self.s["protect"] = np.where(m, 14, self.s["protect"])

    def harvest(self, idx) -> tuple[np.ndarray, np.ndarray]:
        """Harvest sections `idx` (member 0). Returns (stage before, fresh yield t/ha) for those sections."""
        idx = np.asarray(idx, dtype=int)
        s, c = self.s, self.c
        before = s["stage"][0, idx].copy()
        full = self.yield_potential()[0, idx]
        prog = np.clip((s["gdd"][0, idx] - c["gdd_flowering"][idx]) / np.maximum(c["gdd_maturity"][idx] - c["gdd_flowering"][idx], 1), 0, 1)
        y = np.where(before == RIPE, full, np.where((before == GRAIN_FILL) | (before == FLOWERING), 0.4 * full * prog, 0.0))
        m = self._m(idx)
        s["stage"] = np.where(m & (s["stage"] != FALLOW), STUBBLE, s["stage"])
        for k in ("lai", "biomass", "disease", "ripe_days", "loss"):
            s[k] = np.where(m, 0.0, s[k])
        s["height"] = np.where(m, 0.15, s["height"])
        return before, y

    # ------------------------------------------------------------------ persistence
    def dump(self) -> str:
        buf = io.BytesIO()
        np.savez_compressed(buf, **{k: v for k, v in self.s.items()}, rng=np.frombuffer(
            repr(self.rng.bit_generator.state).encode(), dtype=np.uint8))
        return base64.b64encode(buf.getvalue()).decode()

    def load(self, blob: str) -> None:
        z = np.load(io.BytesIO(base64.b64decode(blob)))
        for k in STATE_FIELDS:
            if k in z and z[k].shape == self.s[k].shape:
                self.s[k] = z[k].astype(self.s[k].dtype)
        if "rng" in z:
            import ast
            self.rng.bit_generator.state = ast.literal_eval(bytes(z["rng"]).decode())

    def fork(self, ensemble: int, seed: int) -> "FieldModel":
        m = FieldModel.__new__(FieldModel)
        m.__dict__.update({k: v for k, v in self.__dict__.items() if k not in ("s", "rng", "E", "irrigation")})
        m.E = ensemble
        m.rng = np.random.default_rng(seed)
        m.irrigation = dict(self.irrigation)
        m.s = {k: np.repeat(v[:1], ensemble, axis=0).copy() for k, v in self.s.items()}
        return m
