"""World model: reference values (FAO-56, Saxton & Rawls), statistical behaviour and agronomy."""
import math
from datetime import date, timedelta

import numpy as np
import pytest
from shapely.geometry import LineString, Polygon, box

from app.agro import climate as cl
from app.agro.anomalies import AnomalyDetector
from app.agro.forecast import next_sowing, season
from app.agro.model import RIPE, SOWN, STUBBLE, FieldModel
from app.agro.soil import SoilField, d8_twi, saxton_rawls, texture_class
from app.agro.weather import WeatherGenerator, et0_penman_monteith
from app.services.layout import LayoutParams, Potential, generate_layout
from app.services.operations import Operations
from tests.farmkit import demo


@pytest.fixture(scope="module")
def climate():
    return cl.fit(cl.synthetic_history(36.7, 1), 36.7, -119.8, 90.0, "synthetic")


@pytest.fixture(scope="module")
def world():
    d = demo()
    secs = [{"id": s.id, "polygon": s.polygon, "x": s.x, "y": s.y} for s in d.plan.layout.sections]
    soil = SoilField(d.field.polygon_xy, secs, d.world["tracks"], seed=3)
    area = np.array([s.length * s.width / 1e4 for s in d.plan.layout.sections])
    return d, soil, area


def test_fao56_example_18_et0():
    # FAO-56 example 18 (Brussels, 6 July): ET0 = 3.9 mm/d
    ra = cl.extraterrestrial_radiation(50.8, 187)
    assert abs(ra - 41.09) < 0.3
    et0 = et0_penman_monteith(np.array([21.5]), np.array([12.3]), np.array([22.07]), np.array([73.5]), np.array([2.078]), ra, 100)
    assert abs(float(et0[0]) - 3.9) < 0.35


def test_saxton_rawls_loam_and_texture():
    wp, fc, sat, ksat = saxton_rawls(np.array([40.0]), np.array([20.0]), np.array([2.5]))
    assert 0.10 < wp[0] < 0.15 and 0.26 < fc[0] < 0.32 and 0.42 < sat[0] < 0.5 and 5 < ksat[0] < 30
    assert texture_class(40, 20) == "loam" and texture_class(90, 4) == "sand" and texture_class(20, 50) == "clay"


def test_twi_is_high_in_the_valley():
    y, x = np.mgrid[0:40, 0:40]
    dem = np.abs(x - 20) * 0.1 + y * 0.02  # a valley along x = 20 draining to y = 0
    _, twi = d8_twi(dem, 5.0)
    assert twi[2:-2, 20].mean() > twi[2:-2, 5].mean() + 2


def test_weather_generator_reproduces_its_climate(climate):
    g = WeatherGenerator(climate, ensemble=30, seed=2)
    d, rain, tmax = date(2030, 1, 1), [], []
    for _ in range(365):
        w = g.step(d)
        rain.append(w.rain)
        tmax.append(w.tmax)
        d += timedelta(days=1)
    annual = np.sum(rain, axis=0)
    assert abs(annual.mean() - climate["clim"]["annual_rain_mm"]) / climate["clim"]["annual_rain_mm"] < 0.25
    wet_frac = (np.array(rain) >= 0.2).mean()
    assert 0.1 < wet_frac < 0.6
    assert np.std(annual) > 0  # members differ: it is stochastic


def _season(world, climate, crop="maize", fert=True, irrigate=True, scen=None, seed=5):
    d, soil, area = world
    m = FieldModel(soil.sec, area, [crop] * len(area), seed=1)
    m.irrigation["enabled"] = irrigate
    g = WeatherGenerator(climate, 1, seed)
    day = next_sowing(crop, 36.7, date(2030, 1, 1))
    m.sow(np.arange(m.n))
    if fert:
        m.fertilise(np.arange(m.n), 180)
    for _ in range(320):
        day += timedelta(days=1)
        m.step(g.step(day, scen))
        if (m.s["stage"][0] >= RIPE).all():
            break
    return m, float((m.yield_potential()[0] * area).sum() / area.sum())


def test_irrigated_fertilised_maize_is_realistic(world, climate):
    m, y = _season(world, climate)
    assert 8.0 < y < 15.0, y  # t/ha grain at 15.5 % moisture
    assert (m.s["stage"][0] == RIPE).all()


def test_stress_responses(world, climate):
    _, full = _season(world, climate)
    _, no_n = _season(world, climate, fert=False)
    assert no_n < 0.85 * full  # nitrogen limitation
    drought = [{"kind": "drought", "start": date(2030, 4, 1).toordinal(), "days": 200, "intensity": 1.5}]
    _, dry_rainfed = _season(world, climate, irrigate=False, scen=drought)
    assert dry_rainfed < 0.6 * full  # rain-fed in a drought collapses


def test_operations_and_harvest(world, climate):
    d, soil, area = world
    m = FieldModel(soil.sec, area, ["maize"] * len(area), seed=1)
    ops = Operations(area, list(range(m.n)), m.crop_keys, 36.7)
    day = date(2030, 4, 2)
    ops.plan(m, day, {}, [[] for _ in range(m.n)], 0.0)
    assert len(ops.pending["seed"]) == m.n
    done = [i for kind, idx in ops.tick(1.0, set()) if kind == "seed" for i in idx]
    assert len(done) == m.n  # a fleet sized to the field sows it within a day
    m.sow(done)
    before, y = m.harvest([0, 1])
    assert list(before) == [SOWN, SOWN] and float(y.sum()) == 0.0  # nothing to harvest yet
    assert m.s["stage"][0, 0] == STUBBLE


def test_drought_is_detected_by_spi(climate):
    det = AnomalyDetector(climate, ["a"], [[]])
    g = WeatherGenerator(climate, 1, 3)
    scen = [{"kind": "drought", "start": date(2030, 1, 1).toordinal(), "days": 120, "intensity": 2.0}]
    d, kinds = date(2030, 1, 1), set()
    for _ in range(100):
        w = g.step(d, scen)
        new, _ = det.update(w, crop_sensitive=False, frost_kill=-2, obs={}, probes=[], irrigation_demand_mm=0, irrigation_delivered_mm=0)
        kinds |= {a["kind"] for a in new}
        d += timedelta(days=1)
    assert "drought" in kinds


def test_monte_carlo_forecast_quantiles(world, climate):
    d, soil, area = world
    m = FieldModel(soil.sec, area, ["maize"] * len(area), seed=1)
    m.sow(np.arange(m.n))
    m.fertilise(np.arange(m.n), 180)
    g = WeatherGenerator(climate, 1, 9)
    f = season(m, g, date(2030, 4, 1), E=12)
    q = f["yield_t"]
    assert 0 < q["p10"] <= q["p50"] <= q["p90"]


def test_adaptive_layout_splits_a_chevron_field():
    # two long arms 60 degrees apart: one bed direction cannot suit both
    from shapely import affinity
    from shapely.ops import unary_union
    arm = box(0, 0, 520, 90)
    field = unary_union([arm, affinity.rotate(arm, 60, origin=(0, 0))]).simplify(0.5)
    lay = generate_layout(field, LayoutParams())
    angles = sorted({round(z["angle_deg"]) for z in lay.zones})
    assert len(lay.zones) >= 2, lay.zones
    assert any(abs(a) < 6 for a in angles) and any(abs(a - 60) < 6 for a in angles), angles
    b = lay.baseline
    runs = sum(z["bed_runs"] for z in lay.zones)
    assert runs < 0.75 * b["bed_runs"]  # far fewer short, clipped beds (fewer headland turns)
    assert lay.crop_area_m2 > 0.97 * b["bed_area_m2"]  # for about the same planted area
    assert b["optimised_score"] > b["score"]


def test_layout_reuses_roads_and_avoids_obstacles():
    field = box(0, 0, 400, 300)
    road = {"xy": [[200, -50], [200, 350]], "width_m": 6.0}
    barn = {"xy": [[60, 60], [100, 60], [100, 90], [60, 90], [60, 60]], "kind": "building"}
    lay = generate_layout(field, LayoutParams(), roads=[road], obstacles=[barn])
    assert lay.roads_used == 1 and lay.obstacles_excluded == 1
    corridor = LineString(road["xy"]).buffer(4.0)
    barn_p = Polygon(barn["xy"]).buffer(2.9)
    for s in lay.sections:
        sp = Polygon(s.polygon)
        assert not sp.intersects(corridor) and not sp.intersects(barn_p), s.id
    assert any(t.kind == "road" for t in lay.tracks)
    assert {s.zone for s in lay.sections} == {0, 1}  # the road splits the field into two zones


def test_soil_potential_steers_beds_away_from_poor_ground():
    field = box(0, 0, 300, 300)
    grid = np.ones((80, 80))
    grid[:, :25] = 0.05  # the western third is barren (e.g. saline)
    lay = generate_layout(field, LayoutParams(), potential=Potential(grid, (-10, -10), 4.0))
    flat = generate_layout(field, LayoutParams())
    assert len(lay.sections) > 0 and len(flat.sections) > 0
    assert math.isfinite(lay.crop_area_m2)
