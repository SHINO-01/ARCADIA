"""Stochastic daily weather generator (Richardson-type) + FAO-56 Penman-Monteith ET0.

Vectorised over an ensemble dimension E: the live simulation runs E=1, Monte Carlo forecasts run
E=40 from the same state. Anomaly scenarios (heatwave, drought, wet spell/flood, frost, storm) are
layered on top of the fitted climate so operators can stress-test the farm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from app.agro.climate import VARS, day_length_h, extraterrestrial_radiation, harmonic, solar_elevation

SIGMA = 4.903e-9  # Stefan-Boltzmann, MJ K-4 m-2 d-1


@dataclass
class Day:
    """One simulated day; every field is an array of shape (E,)."""
    date: date
    doy: int
    tmax: np.ndarray
    tmin: np.ndarray
    rain: np.ndarray
    rs: np.ndarray
    rh: np.ndarray
    wind: np.ndarray      # m/s at 2 m
    et0: np.ndarray
    ra: float
    rso: float
    hail: np.ndarray
    scenarios: list[str] = field(default_factory=list)

    @property
    def tmean(self) -> np.ndarray:
        return (self.tmax + self.tmin) / 2

    @property
    def cloud(self) -> np.ndarray:
        return np.clip(1.0 - (self.rs / max(self.rso, 1e-6) - 0.25) / 0.75, 0.0, 1.0)

    def at(self, i: int = 0) -> dict:
        return {"date": self.date.isoformat(), "tmax": round(float(self.tmax[i]), 2), "tmin": round(float(self.tmin[i]), 2),
                "rain": round(float(self.rain[i]), 2), "rs": round(float(self.rs[i]), 2), "rh": round(float(self.rh[i]), 1),
                "wind": round(float(self.wind[i]), 2), "et0": round(float(self.et0[i]), 2), "cloud": round(float(self.cloud[i]), 2),
                "hail": bool(self.hail[i]), "scenarios": list(self.scenarios)}


def sat_vp(t):
    return 0.6108 * np.exp(17.27 * t / (t + 237.3))


def et0_penman_monteith(tmax, tmin, rs, rh, u2, ra: float, elevation: float):
    """FAO-56 reference evapotranspiration (mm/d), eq. 6 with daily Rn (eqs. 37-40)."""
    t = (tmax + tmin) / 2
    delta = 4098 * sat_vp(t) / (t + 237.3) ** 2
    p = 101.3 * ((293 - 0.0065 * elevation) / 293) ** 5.26
    gamma = 0.000665 * p
    es = (sat_vp(tmax) + sat_vp(tmin)) / 2
    ea = np.clip(rh / 100.0, 0.05, 1.0) * es
    rso = (0.75 + 2e-5 * elevation) * ra
    rns = 0.77 * rs
    rnl = SIGMA * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2 * (0.34 - 0.14 * np.sqrt(ea)) * \
        (1.35 * np.clip(rs / max(rso, 1e-6), 0.25, 1.0) - 0.35)
    rn = rns - rnl
    num = 0.408 * delta * rn + gamma * 900 / (t + 273) * u2 * (es - ea)
    return np.clip(num / (delta + gamma * (1 + 0.34 * u2)), 0.0, 15.0)


SCENARIOS = {
    "heatwave": "Heatwave: +6 degC days, warm nights, little rain",
    "drought": "Drought: rain occurrence collapses, hotter and drier air",
    "wet_spell": "Wet spell / flood: persistent heavy rain",
    "frost": "Frost event: night temperatures well below freezing",
    "storm": "Convective storm: torrential rain, gusts, hail",
}


class WeatherGenerator:
    def __init__(self, params: dict, ensemble: int = 1, seed: int = 0):
        self.p = params
        self.E = ensemble
        self.rng = np.random.default_rng(seed)
        self.wet_prev = np.zeros(ensemble, dtype=bool)
        self.z = np.zeros((ensemble, 3))
        self.A = np.asarray(params["A"])
        self.B = np.asarray(params["B"])
        self.lat = float(params["lat"])
        self.elev = float(params.get("elevation", 0.0))

    # state for persistence / forking an ensemble from the live state
    def state(self) -> dict:
        return {"wet_prev": self.wet_prev.tolist(), "z": self.z.tolist(), "rng": self.rng.bit_generator.state}

    def load_state(self, st: dict) -> None:
        self.wet_prev = np.asarray(st["wet_prev"], dtype=bool)
        self.z = np.asarray(st["z"], dtype=float)
        self.rng.bit_generator.state = st["rng"]

    def fork(self, ensemble: int, seed: int) -> "WeatherGenerator":
        g = WeatherGenerator(self.p, ensemble, seed)
        g.wet_prev = np.repeat(self.wet_prev[:1], ensemble)
        g.z = np.repeat(self.z[:1], ensemble, axis=0)
        return g

    def step(self, d: date, scenarios: list[dict] | None = None) -> Day:
        p, E, rng = self.p, self.E, self.rng
        doy = d.timetuple().tm_yday
        m = d.month - 1
        active = [s for s in (scenarios or []) if s["start"] <= d.toordinal() < s["start"] + s["days"]]
        kinds = {s["kind"]: float(s.get("intensity", 1.0)) for s in active}

        p01, p11 = p["p01"][m], p["p11"][m]
        shape, scale = p["gamma_shape"][m], p["gamma_scale"][m]
        if "drought" in kinds:
            f = 1 - 0.85 * kinds["drought"]
            p01, p11 = p01 * f, p11 * f
        if "heatwave" in kinds:
            p01, p11 = p01 * 0.3, p11 * 0.3
        if "wet_spell" in kinds:
            p01, p11 = max(p01, 0.65), max(p11, 0.9)
            scale *= 1 + 1.5 * kinds["wet_spell"]
        pw = np.where(self.wet_prev, p11, p01)
        wet = rng.random(E) < pw
        rain = np.where(wet, rng.gamma(shape, scale, E), 0.0)
        rain = np.where(wet, np.maximum(rain, 0.2), 0.0)

        # multivariate AR(1) residuals -> Tmax, Tmin, Rs
        self.z = self.z @ self.A.T + rng.standard_normal((E, 3)) @ self.B.T
        out = {}
        for i, v in enumerate(VARS):
            mu = np.where(wet, harmonic(p["means"][v]["wet"], doy)[0], harmonic(p["means"][v]["dry"], doy)[0])
            sd = math.sqrt(max(1e-3, float(harmonic(p["stds"][v], doy)[0])))
            out[v] = mu + sd * self.z[:, i]
        tmax, tmin = out["tmax"], np.minimum(out["tmin"], out["tmax"] - 1.0)
        ra = extraterrestrial_radiation(self.lat, doy)
        rso = (0.75 + 2e-5 * self.elev) * ra
        rs = np.clip(out["rs"], 0.12 * ra, rso)
        rh = np.where(wet, p["rh_wet"][m], p["rh_dry"][m]) + rng.normal(0, 6, E)
        u10 = p["wind_mean"][m] * rng.lognormal(-0.5 * p["wind_cv"] ** 2, p["wind_cv"], E)
        hail = np.zeros(E, dtype=bool)

        if "heatwave" in kinds:
            k = kinds["heatwave"]
            tmax, tmin, rs, rh = tmax + 6 * k, tmin + 4 * k, np.minimum(rs * 1.05, rso), rh - 12 * k
        if "drought" in kinds:
            k = kinds["drought"]
            tmax, rh = tmax + 1.5 * k, rh - 15 * k
        if "wet_spell" in kinds:
            rs, rh = rs * 0.7, np.maximum(rh, 85)
        if "frost" in kinds:
            k = kinds["frost"]
            tmin = np.minimum(tmin, -2 - 5 * k + rng.normal(0, 0.8, E))
            tmax = np.minimum(tmax, tmin + 9)
        if "storm" in kinds:
            k = kinds["storm"]
            rain = rain + 35 + 55 * k * rng.random(E)
            u10 = np.maximum(u10, 14 + 10 * k)
            hail = rng.random(E) < 0.5 * k
            rs, rh = rs * 0.55, np.maximum(rh, 90)
        rs = np.clip(rs, 0.1 * ra, rso)
        rh = np.clip(rh, 8, 100)
        u2 = np.clip(u10 * 4.87 / math.log(67.8 * 10 - 5.42), 0.3, 25)
        tmin = np.minimum(tmin, tmax - 0.5)
        et0 = et0_penman_monteith(tmax, tmin, rs, rh, u2, ra, self.elev)
        self.wet_prev = rain >= 0.2
        return Day(date=d, doy=doy, tmax=tmax, tmin=tmin, rain=rain, rs=rs, rh=rh, wind=u2, et0=et0, ra=ra, rso=rso,
                   hail=hail, scenarios=sorted(kinds))


# ----------------------------------------------------------------------------- sub-daily profiles
def hourly_temperature(day: Day, hour: float, i: int = 0) -> float:
    """Diurnal cycle: minimum near sunrise, maximum at 14:30 (cosine segments)."""
    tmax, tmin = float(day.tmax[i]), float(day.tmin[i])
    sunrise = 12 - day_length_h(36.0, day.doy) / 2
    if sunrise <= hour <= 14.5:
        f = (1 - math.cos(math.pi * (hour - sunrise) / (14.5 - sunrise))) / 2
    else:
        h = hour - 14.5 if hour > 14.5 else hour + 24 - 14.5
        f = (1 + math.cos(math.pi * h / (24 - 14.5 + sunrise))) / 2
    return tmin + (tmax - tmin) * f


def irradiance_wm2(day: Day, lat: float, hour: float, i: int = 0) -> float:
    """Global horizontal irradiance at `hour`: the day's Rs spread over the solar elevation profile."""
    hours = np.arange(0, 24, 0.25)
    prof = np.array([max(0.0, math.sin(solar_elevation(lat, day.doy, h))) for h in hours])
    s = prof.sum() * 0.25
    if s <= 0:
        return 0.0
    now = max(0.0, math.sin(solar_elevation(lat, day.doy, hour)))
    return float(day.rs[i]) * 1e6 / 3600 * now / s
