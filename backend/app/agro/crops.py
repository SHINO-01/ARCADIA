"""Crop parameter sets for the field crops ARCADIA can grow.

Real farms grow one crop per field per season (sometimes two in rotation blocks), so the catalogue
is deliberately short. Parameters come from FAO-56 (Kc, rooting depth, depletion fraction p),
FAO-33 (yield response Ky), Maas-Hoffman salinity tolerance and the crop-modelling literature
(thermal time, radiation-use efficiency, harvest index, critical N dilution curves).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Crop:
    key: str
    name: str
    # phenology: thermal time from sowing (degC.d above t_base, capped at t_cut)
    t_base: float
    t_cut: float
    gdd_emergence: float
    gdd_flowering: float
    gdd_maturity: float
    # growth temperature response (beta function between t_min_g and t_max_g, optimum plateau)
    t_opt_lo: float
    t_opt_hi: float
    t_max_g: float
    # canopy and biomass
    lai_max: float
    k_ext: float          # light extinction coefficient (Beer's law)
    rue: float            # g dry matter per MJ intercepted PAR
    hi: float             # harvest index (dry matter)
    dm_fraction: float    # dry-matter fraction of the marketable product (grain moisture / fruit)
    height_max: float     # m
    # water (FAO-56)
    kc_ini: float
    kc_mid: float
    kc_end: float
    root_min: float       # m
    root_max: float       # m
    p: float              # depletion fraction before stress
    ky: float             # yield response to water deficit (FAO-33)
    # nitrogen: critical N dilution %N = a * W^-b (W in t/ha); fixers draw from air
    n_a: float
    n_b: float
    n_fixing: bool
    # hazards
    frost_kill: float     # degC Tmin that damages the established canopy
    heat_crit: float      # degC Tmax that hurts pollination / fruit set around flowering
    ec_threshold: float   # dS/m soil salinity before yield loss (Maas-Hoffman)
    ec_slope: float       # % yield lost per dS/m above the threshold
    waterlog_tolerance: float  # days of saturation before damage
    # calendar: sowing months (1-12) in the northern hemisphere (shifted 6 months in the south)
    sow_months: tuple[int, ...]
    # economics (indicative)
    price_per_t: float    # USD per t fresh marketable product
    cost_per_ha: float    # USD direct costs
    unit: str = "t"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sow_months"] = list(self.sow_months)
        return d


CROPS: dict[str, Crop] = {
    "maize": Crop(
        key="maize", name="Maize (grain)", t_base=8.0, t_cut=34.0, gdd_emergence=70, gdd_flowering=850,
        gdd_maturity=1600, t_opt_lo=24, t_opt_hi=32, t_max_g=42, lai_max=5.5, k_ext=0.65, rue=3.8, hi=0.50,
        dm_fraction=0.845, height_max=2.3, kc_ini=0.3, kc_mid=1.2, kc_end=0.35, root_min=0.15, root_max=1.2,
        p=0.55, ky=1.25, n_a=3.40, n_b=0.37, n_fixing=False, frost_kill=-1.5, heat_crit=35.0, ec_threshold=1.7,
        ec_slope=12.0, waterlog_tolerance=3.0, sow_months=(4, 5), price_per_t=200.0, cost_per_ha=950.0),
    "wheat": Crop(
        key="wheat", name="Winter wheat", t_base=0.0, t_cut=30.0, gdd_emergence=140, gdd_flowering=1250,
        gdd_maturity=2100, t_opt_lo=15, t_opt_hi=24, t_max_g=35, lai_max=6.0, k_ext=0.50, rue=2.8, hi=0.45,
        dm_fraction=0.87, height_max=0.9, kc_ini=0.4, kc_mid=1.15, kc_end=0.3, root_min=0.15, root_max=1.5,
        p=0.55, ky=1.15, n_a=5.35, n_b=0.442, n_fixing=False, frost_kill=-12.0, heat_crit=32.0, ec_threshold=6.0,
        ec_slope=7.1, waterlog_tolerance=5.0, sow_months=(10, 11), price_per_t=230.0, cost_per_ha=750.0),
    "soybean": Crop(
        key="soybean", name="Soybean", t_base=10.0, t_cut=34.0, gdd_emergence=90, gdd_flowering=650,
        gdd_maturity=1350, t_opt_lo=22, t_opt_hi=30, t_max_g=40, lai_max=5.0, k_ext=0.50, rue=2.0, hi=0.42,
        dm_fraction=0.87, height_max=0.9, kc_ini=0.4, kc_mid=1.15, kc_end=0.5, root_min=0.15, root_max=1.0,
        p=0.5, ky=0.85, n_a=5.10, n_b=0.32, n_fixing=True, frost_kill=-1.0, heat_crit=35.0, ec_threshold=5.0,
        ec_slope=20.0, waterlog_tolerance=3.0, sow_months=(5, 6), price_per_t=420.0, cost_per_ha=700.0),
    "tomato": Crop(
        key="tomato", name="Processing tomato", t_base=10.0, t_cut=32.0, gdd_emergence=100, gdd_flowering=600,
        gdd_maturity=1400, t_opt_lo=20, t_opt_hi=28, t_max_g=37, lai_max=4.5, k_ext=0.65, rue=2.6, hi=0.55,
        dm_fraction=0.06, height_max=0.7, kc_ini=0.6, kc_mid=1.15, kc_end=0.8, root_min=0.2, root_max=1.0,
        p=0.4, ky=1.05, n_a=4.50, n_b=0.33, n_fixing=False, frost_kill=0.0, heat_crit=36.0, ec_threshold=2.5,
        ec_slope=9.9, waterlog_tolerance=2.0, sow_months=(3, 4, 5), price_per_t=90.0, cost_per_ha=4500.0),
}

DEFAULT_CROP = "maize"
CROP_HEIGHT_M_ALL = {k: c.height_max for k, c in CROPS.items()}


def get_crop(key: str) -> Crop:
    return CROPS.get(key, CROPS[DEFAULT_CROP])


def sow_months_for(crop: Crop, lat: float) -> tuple[int, ...]:
    """Sowing months for the hemisphere of `lat`."""
    if lat >= 0:
        return crop.sow_months
    return tuple(((m + 5) % 12) + 1 for m in crop.sow_months)
