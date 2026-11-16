"""Climate calibration: weather-generator parameters fitted to the farm's own daily history.

Source: Open-Meteo's ERA5 archive (10 full years of daily data at the field centroid). Fitted:

  * rainfall occurrence - first-order Markov chain per month: p01 = P(wet | dry yesterday),
    p11 = P(wet | wet yesterday), wet day = rain >= 0.2 mm (Richardson 1981);
  * rainfall amount     - two-parameter gamma per month (Thom's maximum-likelihood estimator);
  * Tmax / Tmin / Rs    - seasonal means for wet and dry days and seasonal standard deviations as
    2-harmonic Fourier series; standardised residuals follow a multivariate AR(1)
    z_t = A z_{t-1} + B e_t with A = M1 M0^-1 and B B' = M0 - A M1' (Matalas 1967);
  * humidity and wind   - monthly wet/dry means and a lognormal wind coefficient of variation;
  * climatologies for anomaly detection - daily Tmax/Tmin mean and SD, monthly gamma fits of
    30-day rainfall totals for the Standardised Precipitation Index, the daily rain 99th percentile.

Offline, a latitude-based synthetic climate is used instead (flagged in `source`).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

WET_MM = 0.2
VARS = ("tmax", "tmin", "rs")


def _design(doy: np.ndarray) -> np.ndarray:
    w = 2 * np.pi * doy / 365.25
    return np.column_stack([np.ones_like(w), np.cos(w), np.sin(w), np.cos(2 * w), np.sin(2 * w)])


def harmonic(coef, doy) -> np.ndarray:
    return _design(np.atleast_1d(np.asarray(doy, dtype=float))) @ np.asarray(coef)


def _fit_harmonic(doy: np.ndarray, y: np.ndarray) -> list[float]:
    ok = np.isfinite(y)
    if ok.sum() < 20:
        return [float(np.nanmean(y)) if ok.any() else 0.0, 0, 0, 0, 0]
    coef, *_ = np.linalg.lstsq(_design(doy[ok]), y[ok], rcond=None)
    return [float(c) for c in coef]


def _gamma_mle(x: np.ndarray) -> tuple[float, float]:
    """Thom (1958) maximum-likelihood approximation for the gamma shape; scale = mean / shape."""
    x = x[x > 0]
    if len(x) < 5:
        return 0.8, max(1.0, float(x.mean()) if len(x) else 5.0)
    m = x.mean()
    a = math.log(m) - np.log(x).mean()
    shape = (1 + math.sqrt(1 + 4 * a / 3)) / (4 * a) if a > 0 else 1.0
    shape = float(np.clip(shape, 0.3, 5.0))
    return shape, float(m / shape)


def fit(days: dict, lat: float, lon: float, elevation: float, source: str) -> dict:
    """days: arrays 'date' (datetime.date), 'tmax', 'tmin', 'rain', 'rs' (MJ/m2/d), optional 'rh', 'wind' (m/s)."""
    d = np.array(days["date"])
    doy = np.array([x.timetuple().tm_yday for x in d], dtype=float)
    month = np.array([x.month for x in d])
    rain = np.nan_to_num(np.asarray(days["rain"], dtype=float))
    wet = rain >= WET_MM

    p01, p11, gshape, gscale = [], [], [], []
    for mth in range(1, 13):
        idx = np.where(month == mth)[0]
        idx = idx[idx > 0]
        prev = wet[idx - 1]
        cur = wet[idx]
        p01.append(float(np.clip(cur[~prev].mean() if (~prev).any() else 0.2, 0.005, 0.95)))
        p11.append(float(np.clip(cur[prev].mean() if prev.any() else 0.4, 0.02, 0.98)))
        s, sc = _gamma_mle(rain[(month == mth) & wet])
        gshape.append(s)
        gscale.append(sc)

    means: dict[str, dict] = {}
    stds: dict[str, list[float]] = {}
    resid = {}
    for v in VARS:
        y = np.asarray(days[v], dtype=float)
        mw, md = _fit_harmonic(doy[wet], y[wet]), _fit_harmonic(doy[~wet], y[~wet])
        means[v] = {"wet": mw, "dry": md}
        mu = np.where(wet, harmonic(mw, doy), harmonic(md, doy))
        r = y - mu
        sd_coef = _fit_harmonic(doy, r ** 2)
        sd = np.sqrt(np.clip(harmonic(sd_coef, doy), 1e-3, None))
        stds[v] = sd_coef
        resid[v] = r / sd
    Z = np.column_stack([np.nan_to_num(resid[v]) for v in VARS])
    M0 = np.cov(Z[1:].T, bias=True)
    M1 = (Z[1:].T @ Z[:-1]) / (len(Z) - 1)
    A = M1 @ np.linalg.inv(M0)
    C = M0 - A @ M1.T
    w, V = np.linalg.eigh((C + C.T) / 2)
    B = V @ np.diag(np.sqrt(np.clip(w, 1e-6, None)))

    rh = np.asarray(days.get("rh", np.full(len(d), np.nan)), dtype=float)
    wind = np.asarray(days.get("wind", np.full(len(d), np.nan)), dtype=float)
    rh_wet = [float(np.nanmean(rh[(month == m) & wet])) if np.isfinite(rh[(month == m) & wet]).any() else 70.0 for m in range(1, 13)]
    rh_dry = [float(np.nanmean(rh[(month == m) & ~wet])) if np.isfinite(rh[(month == m) & ~wet]).any() else 55.0 for m in range(1, 13)]
    wind_mean = [float(np.nanmean(wind[month == m])) if np.isfinite(wind[month == m]).any() else 2.0 for m in range(1, 13)]
    wv = wind[np.isfinite(wind)]
    wind_cv = float(np.clip(wv.std() / wv.mean(), 0.15, 0.9)) if len(wv) > 30 else 0.4

    # climatologies for anomaly detection
    tmax, tmin = np.asarray(days["tmax"], float), np.asarray(days["tmin"], float)
    clim = {
        "tmax_mean": _fit_harmonic(doy, tmax), "tmin_mean": _fit_harmonic(doy, tmin),
        "tmax_sd": _fit_harmonic(doy, (tmax - harmonic(_fit_harmonic(doy, tmax), doy)) ** 2),
        "tmin_sd": _fit_harmonic(doy, (tmin - harmonic(_fit_harmonic(doy, tmin), doy)) ** 2),
    }
    r30 = np.convolve(rain, np.ones(30), mode="full")[: len(rain)]
    spi = []
    for m in range(1, 13):
        x = r30[(month == m) & (np.arange(len(rain)) >= 29)][::7]  # weekly samples cut autocorrelation
        p0 = float((x <= 0.1).mean()) if len(x) else 0.0
        s, sc = _gamma_mle(x[x > 0.1]) if (x > 0.1).sum() >= 5 else (1.0, max(1.0, float(x.mean()) if len(x) else 10.0))
        spi.append([s, sc, p0])
    clim["spi30"] = spi
    clim["rain_p99"] = float(np.percentile(rain[wet], 99)) if wet.sum() > 20 else 40.0
    clim["annual_rain_mm"] = float(rain.sum() / max(1.0, len(rain) / 365.25))
    clim["annual_tmean"] = float(np.mean((tmax + tmin) / 2))

    return {"source": source, "lat": lat, "lon": lon, "elevation": float(elevation), "years": round(len(d) / 365.25, 1),
            "p01": p01, "p11": p11, "gamma_shape": gshape, "gamma_scale": gscale, "means": means, "stds": stds,
            "A": A.tolist(), "B": B.tolist(), "rh_wet": rh_wet, "rh_dry": rh_dry, "wind_mean": wind_mean,
            "wind_cv": wind_cv, "clim": clim}


def fetch_open_meteo(lat: float, lon: float, years: int = 10, timeout: float = 30.0) -> dict | None:
    """Daily ERA5 history from Open-Meteo (no key). Returns `fit` input or None when unreachable."""
    import httpx

    end = date(date.today().year - 1, 12, 31)
    start = date(end.year - years + 1, 1, 1)
    full = "temperature_2m_max,temperature_2m_min,precipitation_sum,shortwave_radiation_sum,relative_humidity_2m_mean,wind_speed_10m_mean"
    basic = "temperature_2m_max,temperature_2m_min,precipitation_sum,shortwave_radiation_sum"
    for daily in (full, basic):
        try:
            r = httpx.get("https://archive-api.open-meteo.com/v1/archive", timeout=timeout, params={
                "latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": end.isoformat(),
                "daily": daily, "timezone": "UTC", "wind_speed_unit": "ms"})
            if r.status_code != 200:
                continue
            j = r.json()
            dd = j["daily"]
            out = {"date": [date.fromisoformat(t) for t in dd["time"]],
                   "tmax": dd["temperature_2m_max"], "tmin": dd["temperature_2m_min"],
                   "rain": dd["precipitation_sum"], "rs": dd["shortwave_radiation_sum"],
                   "elevation": j.get("elevation", 0.0)}
            if "relative_humidity_2m_mean" in dd:
                out["rh"] = dd["relative_humidity_2m_mean"]
            if "wind_speed_10m_mean" in dd:
                out["wind"] = dd["wind_speed_10m_mean"]
            for k in ("tmax", "tmin", "rain", "rs", "rh", "wind"):
                if k in out:
                    out[k] = [np.nan if v is None else v for v in out[k]]
            return out
        except Exception:  # noqa: BLE001 - offline or rate-limited: caller falls back
            continue
    return None


def synthetic_history(lat: float, seed: int = 0, years: int = 10) -> dict:
    """Plausible daily history for a latitude when no data source is reachable."""
    rng = np.random.default_rng(seed)
    n = int(years * 365.25)
    start = date(2015, 1, 1)
    ds = [start + timedelta(days=i) for i in range(n)]
    doy = np.array([x.timetuple().tm_yday for x in ds], float)
    south = lat < 0
    phase = 2 * np.pi * (doy - (200 if not south else 17)) / 365.25
    a = abs(lat)
    tmean = 27.5 - 0.42 * a + (2 + 0.28 * a) * np.cos(phase) + rng.normal(0, 2.2, n)
    dtr = 11 + 2 * np.cos(phase)
    wet_season = 0.5 - 0.35 * np.cos(phase) if 28 < a < 45 else np.full(n, 0.3)  # mediterranean winter rain
    rain = np.zeros(n)
    wetp = 0.0
    for i in range(n):
        p = (0.12 + 0.3 * wet_season[i]) if wetp == 0 else (0.35 + 0.3 * wet_season[i])
        if rng.random() < p:
            rain[i] = rng.gamma(0.8, 9.0)
        wetp = rain[i] > WET_MM
    ra = np.array([extraterrestrial_radiation(lat, int(dd)) for dd in doy])
    rs = ra * np.clip(0.72 - 0.4 * (rain > WET_MM) + rng.normal(0, 0.06, n), 0.2, 0.78)
    return {"date": ds, "tmax": tmean + dtr / 2 - 2 * (rain > 1), "tmin": tmean - dtr / 2, "rain": rain, "rs": rs,
            "rh": np.clip(55 + 25 * (rain > WET_MM) + rng.normal(0, 6, n), 15, 100), "wind": np.clip(rng.lognormal(0.7, 0.4, n), 0.3, 12),
            "elevation": 50.0}


def calibrate(lat: float, lon: float, seed: int = 0) -> dict:
    hist = fetch_open_meteo(lat, lon)
    if hist is not None and len(hist["date"]) > 700:
        return fit(hist, lat, lon, hist.get("elevation", 0.0), f"Open-Meteo ERA5 {hist['date'][0].year}-{hist['date'][-1].year}")
    hist = synthetic_history(lat, seed)
    return fit(hist, lat, lon, hist["elevation"], "synthetic (offline) latitude climate")


# ----------------------------------------------------------------------------- astronomy (FAO-56)
def extraterrestrial_radiation(lat: float, doy: int) -> float:
    """Ra, MJ m-2 d-1 (FAO-56 eq. 21)."""
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    return 24 * 60 / math.pi * 0.0820 * dr * (ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))


def day_length_h(lat: float, doy: int) -> float:
    phi = math.radians(lat)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    return 24 / math.pi * math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))


def solar_elevation(lat: float, doy: int, hour: float) -> float:
    """Solar elevation angle (radians) at local solar `hour`."""
    phi = math.radians(lat)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    h = math.radians(15 * (hour - 12))
    return math.asin(max(-1.0, min(1.0, math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.cos(h))))
