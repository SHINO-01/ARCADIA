"""Soil and terrain: spatially correlated property maps over the field.

Each property is the regional mean (ISRIC SoilGrids at the centroid when reachable) plus
Gaussian-random-field variability with a realistic correlation range (spectral synthesis with a
Gaussian covariance). Derived quantities:

  * hydraulics      - Saxton & Rawls (2006) pedotransfer: wilting point, field capacity,
                      saturation and saturated conductivity from sand, clay and organic matter;
  * terrain         - D8 flow accumulation and the topographic wetness index
                      TWI = ln(a / tan beta): where water collects after rain;
  * runoff          - SCS curve number from the hydrologic soil group (Ksat) and slope;
  * salinity        - background ECe plus rare saline patches favoured in low ground;
  * compaction      - wheel traffic is confined to the tracks (controlled traffic): beds stay loose.

Everything is sampled onto the bed sections (mean of grid cells inside each section polygon) and
kept as rasters for the map overlays.
"""
from __future__ import annotations

import math

import numpy as np
from shapely import contains_xy
from shapely.geometry import Polygon

DEFAULT_BASE = {"sand": 40.0, "clay": 22.0, "om": 1.8, "ph": 7.0, "source": "default loam"}


def fetch_soilgrids(lat: float, lon: float, timeout: float = 30.0, attempts: int = 3) -> dict | None:
    """Topsoil (0-15 cm) means from ISRIC SoilGrids v2, cached on disk. None when unreachable.
    The public service is slow and occasionally hangs, hence short timeouts and retries."""
    import json
    import os
    from pathlib import Path

    import httpx

    cache = Path(os.environ.get("ARCADIA_TILE_CACHE", "/tmp/arcadia_tiles")) / f"soilgrids_{lat:.3f}_{lon:.3f}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    params = [("lon", lon), ("lat", lat), ("property", "sand"), ("property", "clay"), ("property", "soc"),
              ("property", "phh2o"), ("depth", "0-5cm"), ("depth", "5-15cm"), ("value", "mean")]
    for _ in range(attempts):
        try:
            r = httpx.get("https://rest.isric.org/soilgrids/v2.0/properties/query", timeout=timeout, params=params)
            if r.status_code != 200:
                continue
            vals: dict[str, list[float]] = {}
            for layer in r.json()["properties"]["layers"]:
                d = layer["unit_measure"]["d_factor"]
                v = [x["values"]["mean"] for x in layer["depths"] if x["values"]["mean"] is not None]
                if v:
                    vals[layer["name"]] = [x / d for x in v]
            if not {"sand", "clay"} <= vals.keys():
                # unmapped here (urban core, water): look a few hundred metres around for mapped soil
                if attempts > 0 and not getattr(fetch_soilgrids, "_nested", False):
                    fetch_soilgrids._nested = True  # type: ignore[attr-defined]
                    try:
                        for dlat, dlon in ((0.004, 0), (-0.004, 0), (0, 0.005), (0, -0.005)):
                            near = fetch_soilgrids(lat + dlat, lon + dlon, timeout, 1)
                            if near:
                                near["source"] += " (nearest mapped point)"
                                return near
                    finally:
                        fetch_soilgrids._nested = False  # type: ignore[attr-defined]
                return None
            soc = float(np.mean(vals.get("soc", [10.0])))  # g/kg
            out = {"sand": float(np.mean(vals["sand"])), "clay": float(np.mean(vals["clay"])), "om": round(soc * 1.724 / 10, 2),
                   "ph": float(np.mean(vals.get("phh2o", [7.0]))), "source": "ISRIC SoilGrids v2 (0-15 cm)"}
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(out))
            except OSError:
                pass
            return out
        except Exception:  # noqa: BLE001 - timeout or network: retry
            continue
    return None


def grf(shape: tuple[int, int], cell: float, range_m: float, rng: np.random.Generator) -> np.ndarray:
    """Zero-mean, unit-variance Gaussian random field with a Gaussian covariance of practical range
    `range_m` (spectral synthesis on a padded grid to avoid wrap-around)."""
    ny, nx = shape
    py, px = ny + int(range_m / cell) + 8, nx + int(range_m / cell) + 8
    noise = rng.standard_normal((py, px))
    ky = np.fft.fftfreq(py, d=cell)[:, None]
    kx = np.fft.fftfreq(px, d=cell)[None, :]
    a = range_m / math.sqrt(3)  # practical range of the Gaussian model
    spec = np.exp(-(np.pi * a) ** 2 * (kx ** 2 + ky ** 2))
    f = np.real(np.fft.ifft2(np.fft.fft2(noise) * np.sqrt(spec)))[:ny, :nx]
    return (f - f.mean()) / (f.std() + 1e-12)


def saxton_rawls(sand_pct, clay_pct, om_pct):
    """Wilting point, field capacity, saturation (m3/m3) and Ksat (mm/h) - Saxton & Rawls 2006."""
    S, C, OM = sand_pct / 100.0, clay_pct / 100.0, np.clip(om_pct, 0.1, 8.0)
    t1500 = -0.024 * S + 0.487 * C + 0.006 * OM + 0.005 * S * OM - 0.013 * C * OM + 0.068 * S * C + 0.031
    wp = t1500 + (0.14 * t1500 - 0.02)
    t33 = -0.251 * S + 0.195 * C + 0.011 * OM + 0.006 * S * OM - 0.027 * C * OM + 0.452 * S * C + 0.299
    fc = t33 + (1.283 * t33 ** 2 - 0.374 * t33 - 0.015)
    ts33 = 0.278 * S + 0.034 * C + 0.022 * OM - 0.018 * S * OM - 0.027 * C * OM - 0.584 * S * C + 0.078
    s33 = ts33 + (0.636 * ts33 - 0.107)
    sat = fc + s33 - 0.097 * S + 0.043
    fc = np.clip(fc, 0.08, 0.48)
    wp = np.clip(wp, 0.02, fc - 0.04)
    sat = np.clip(sat, fc + 0.03, 0.6)
    lam = (np.log(fc) - np.log(wp)) / (math.log(1500) - math.log(33))
    ksat = np.clip(1930 * np.clip(sat - fc, 1e-4, None) ** (3 - lam), 0.5, 250.0)
    return wp, fc, sat, ksat


def d8_twi(dem: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """D8 flow accumulation (cells draining through each cell, itself included) and the
    topographic wetness index ln(a / tan beta)."""
    ny, nx = dem.shape
    pad = np.pad(dem, 1, mode="edge")
    offs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    best_drop = np.zeros_like(dem)
    best = np.full(dem.shape, -1)
    for k, (dy, dx) in enumerate(offs):
        nb = pad[1 + dy: 1 + dy + ny, 1 + dx: 1 + dx + nx]
        drop = (dem - nb) / (cell * math.hypot(dy, dx))
        better = drop > best_drop
        best_drop = np.where(better, drop, best_drop)
        best = np.where(better, k, best)
    acc = np.ones(dem.size)
    order = np.argsort(-dem, axis=None)
    flat_best = best.ravel()
    for idx in order:  # highest first: pass accumulated area downslope
        k = flat_best[idx]
        if k < 0:
            continue
        y, x = divmod(int(idx), nx)
        dy, dx = offs[k]
        yy, xx = y + dy, x + dx
        if 0 <= yy < ny and 0 <= xx < nx:
            acc[yy * nx + xx] += acc[idx]
    acc = acc.reshape(dem.shape)
    tanb = np.maximum(best_drop, 0.001)
    twi = np.log(acc * cell / tanb)
    return acc, twi


class SoilField:
    """Soil/terrain rasters over the field and their per-section means."""

    def __init__(self, field_xy: Polygon, sections: list[dict], tracks: list[dict], seed: int = 0,
                 base: dict | None = None, cell: float = 4.0):
        self.base = dict(base or DEFAULT_BASE)
        rng = np.random.default_rng(seed + 1009)
        minx, miny, maxx, maxy = field_xy.buffer(8).bounds
        nx, ny = int(math.ceil((maxx - minx) / cell)), int(math.ceil((maxy - miny) / cell))
        self.cell, self.origin_xy, self.shape = cell, (minx, miny), (ny, nx)
        xs = minx + (np.arange(nx) + 0.5) * cell
        ys = miny + (np.arange(ny) + 0.5) * cell
        X, Y = np.meshgrid(xs, ys)
        self.X, self.Y = X, Y
        self.mask = contains_xy(field_xy, X, Y)

        # relief: a gentle regional slope + rolling relief + micro-relief (m)
        slope = rng.uniform(0.002, 0.008)
        aspect = rng.uniform(0, 2 * np.pi)
        dem = slope * (X * math.cos(aspect) + Y * math.sin(aspect))
        dem += 0.9 * grf(self.shape, cell, 160, rng) + 0.25 * grf(self.shape, cell, 45, rng)
        self.dem = dem - dem[self.mask].mean()
        self.acc, self.twi = d8_twi(self.dem, cell)
        low = -(self.dem - self.dem[self.mask].mean()) / (self.dem[self.mask].std() + 1e-9)  # >0 in hollows

        b = self.base
        self.clay = np.clip(b["clay"] + 5.0 * grf(self.shape, cell, 110, rng) + 2.0 * low, 3, 65)
        self.sand = np.clip(b["sand"] - 0.6 * (self.clay - b["clay"]) + 5.0 * grf(self.shape, cell, 140, rng), 3, 92)
        tot = self.sand + self.clay
        over = tot > 95
        self.sand = np.where(over, self.sand * 95 / tot, self.sand)
        self.clay = np.where(over, self.clay * 95 / tot, self.clay)
        self.om = np.clip(b["om"] * np.exp(0.22 * grf(self.shape, cell, 90, rng) + 0.08 * low), 0.3, 8.0)
        self.ph = np.clip(b["ph"] + 0.3 * grf(self.shape, cell, 120, rng), 4.0, 9.0)
        patch = grf(self.shape, cell, 60, rng) + 0.5 * low
        self.ec = np.clip(0.6 * np.exp(0.25 * grf(self.shape, cell, 100, rng)) + np.where(patch > 1.7, rng.uniform(2.5, 6.5) * (patch - 1.7), 0.0), 0.2, 16.0)
        self.wp, self.fc, self.sat, self.ksat = saxton_rawls(self.sand, self.clay, self.om)

        # wheel traffic only on the tracks (controlled traffic): compaction raster
        from shapely.geometry import LineString
        from shapely.ops import unary_union
        lanes = unary_union([LineString(t["coords"]).buffer(t.get("width", 3) / 2) for t in tracks]) if tracks else None
        self.compaction = contains_xy(lanes, X, Y).astype(float) if lanes is not None else np.zeros(self.shape)

        # per-section means
        self.section_ids = [s["id"] for s in sections]
        self.cells_of: list[np.ndarray] = []
        for s in sections:
            poly = Polygon(s["polygon"]) if s.get("polygon") else None
            m = contains_xy(poly, X, Y) if poly is not None else None
            if m is None or not m.any():  # tiny section: nearest cell
                iy = int(np.clip((s["y"] - miny) // cell, 0, ny - 1))
                ix = int(np.clip((s["x"] - minx) // cell, 0, nx - 1))
                m = np.zeros(self.shape, bool)
                m[iy, ix] = True
            self.cells_of.append(np.flatnonzero(m))
        self.sec = {k: self._mean(getattr(self, k)) for k in
                    ("sand", "clay", "om", "ph", "ec", "wp", "fc", "sat", "ksat", "dem", "twi", "compaction")}
        twi_f = self.twi[self.mask]
        self.sec["twi_z"] = (self.sec["twi"] - twi_f.mean()) / (twi_f.std() + 1e-9)
        # SCS curve number: hydrologic group from Ksat (A > 30, B 10-30, C 3-10, D < 3 mm/h), row crop
        k = self.sec["ksat"]
        cn = np.where(k > 30, 67, np.where(k > 10, 78, np.where(k > 3, 85, 89))).astype(float)
        self.sec["cn"] = cn
        # mineral N (kg/ha) and a daily mineralisation potential from organic matter
        self.sec["n0"] = 25 + 9 * self.sec["om"]
        self.sec["n_min_rate"] = 0.32 * self.sec["om"]

    def _mean(self, raster: np.ndarray) -> np.ndarray:
        flat = raster.ravel()
        return np.array([float(flat[c].mean()) for c in self.cells_of])

    def summary(self) -> dict:
        m = self.mask
        out = {"source": self.base.get("source"), "cell_m": self.cell}
        for k in ("sand", "clay", "om", "ph", "ec", "ksat", "twi"):
            v = getattr(self, k)[m]
            out[k] = {"mean": round(float(v.mean()), 3), "p10": round(float(np.percentile(v, 10)), 3),
                      "p90": round(float(np.percentile(v, 90)), 3)}
        out["taw_mm_per_m"] = round(float(((self.fc - self.wp)[m]).mean() * 1000), 1)
        out["relief_m"] = round(float(np.ptp(self.dem[m])), 2)
        out["texture"] = texture_class(float(self.sand[m].mean()), float(self.clay[m].mean()))
        return out

    def layer(self, name: str) -> np.ndarray:
        if name == "taw":
            return (self.fc - self.wp) * 1000
        if name == "elevation":
            return self.dem
        return getattr(self, name)


def texture_class(sand: float, clay: float) -> str:
    """USDA texture triangle (main classes)."""
    silt = 100 - sand - clay
    if clay >= 40:
        return "clay" if sand <= 45 and silt < 40 else ("silty clay" if silt >= 40 else "sandy clay")
    if clay >= 27:
        return "clay loam" if sand <= 45 and sand > 20 else ("silty clay loam" if sand <= 20 else "sandy clay loam")
    if silt >= 80 and clay < 12:
        return "silt"
    if silt >= 50:
        return "silt loam"
    if sand >= 85:
        return "sand"
    if sand >= 70:
        return "loamy sand"
    if clay < 20 and sand > 52 or clay < 7:
        return "sandy loam"
    return "loam"


def productivity(sf: "SoilField") -> np.ndarray:
    """Relative productivity (1 = field average) for layout optimisation: plant-available water,
    salinity (generic threshold 3 dS/m, 10 %/dS/m), waterlogging risk in wet hollows (TWI) and
    organic matter."""
    taw = (sf.fc - sf.wp) * 1000
    f_taw = np.clip(0.75 + 0.25 * (taw - 80) / 80, 0.6, 1.1)
    f_sal = np.clip(1 - 0.10 * (sf.ec - 3.0), 0.2, 1.0)
    t = sf.twi[sf.mask] if sf.mask.any() else sf.twi.ravel()
    z = (sf.twi - t.mean()) / (t.std() + 1e-9)
    f_wet = np.where(z > 1.2, np.clip(1 - 0.25 * (z - 1.2), 0.5, 1.0), 1.0)
    f_om = np.clip(0.9 + 0.05 * sf.om, 0.9, 1.15)
    P = f_taw * f_sal * f_wet * f_om
    m = P[sf.mask].mean() if sf.mask.any() else P.mean()
    return P / max(m, 1e-9)
