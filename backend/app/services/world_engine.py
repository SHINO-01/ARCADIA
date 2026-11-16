"""Runtime of the agronomic world model for the live farm.

One engine per API process (it lives next to the simulation bridge). It owns the field's ground
truth and advances it on a compressed clock (`day_seconds` real seconds per simulated day):

  start of day  weather generated for the day (stochastic, climate-calibrated, scenarios applied)
  during day    hourly temperature/irradiance for display, sun position and sky for Webots,
                virtual-fleet field operations progressing continuously
  end of day    field model step (water, crop, nitrogen, weeds, disease, hazards), the farm's
                phenology record, soil probes, statistical anomaly detection, daily records,
                operations planning, snapshot; Monte Carlo forecast every 7 days

Observations come from the robots: a scout UAV's `section_seen` (proximity mock CV) becomes a
noisy multispectral measurement; work events (seeded/fertilised/weeded/sprayed/harvested) change
the truth and are booked (yields only for what was actually ripe).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from sqlalchemy import select

from app.agro import forecast as fc
from app.agro.anomalies import AnomalyDetector
from app.agro.climate import solar_elevation
from app.agro.crops import CROPS, DEFAULT_CROP, sow_months_for
from app.agro.model import (FALLOW, FLOWERING, GRAIN_FILL, RIPE, SOWN, STAGE_NAMES, STUBBLE, VEGETATIVE, FieldModel)
from app.agro.observe import probe_readings, uav_observe
from app.agro.soil import SoilField
from app.agro.weather import SCENARIOS, WeatherGenerator, hourly_temperature, irradiance_wm2
from app.core.db import session_scope
from app.core.redis import get_redis
from app.models import Anomaly, Farm, FarmDaily, FieldSection, WeatherDaily, WorldState, YieldRecord
from app.schemas import Command
from app.services import fleet
from app.services.field import field_of_farm
from app.services.operations import CLAIM_TTL_S, KIND_OF_EVENT, N_RATE, Operations

log = logging.getLogger("arcadia.world")

KEY_OPS = "arcadia:ops:pending"
KEY_CLAIMS = "arcadia:ops:claims"
KEY_NOW = "arcadia:world:now"
MODEL_SCENARIOS = {"irrigation_failure": "Irrigation failure: pump down, no water delivered",
                   "pest_outbreak": "Pest / disease outbreak seeded in the canopy"}
ALL_SCENARIOS = {**SCENARIOS, **MODEL_SCENARIOS}


def section_neighbours(polys: list[Polygon], gap: float = 3.2) -> list[list[int]]:
    tree = STRtree(polys)
    out = []
    for i, p in enumerate(polys):
        cand = tree.query(p.buffer(gap))
        out.append([int(j) for j in cand if int(j) != i and polys[int(j)].distance(p) <= gap])
    return out


def build_world(farm: Farm, rows: list[FieldSection], crop_keys: list[str] | None = None, seed: int | None = None):
    """Soil field + field model for a planned farm (shared by the engine and the planning APIs)."""
    order = {s["id"]: k for k, s in enumerate(farm.world_config["sections"])}
    rows = sorted(rows, key=lambda r: order.get(r.id, 1e9))
    secs = [{"id": r.id, "polygon": r.polygon, "x": r.x, "y": r.y} for r in rows]
    setup = farm.setup or {}
    soil = SoilField(field_of_farm(farm).polygon_xy, secs, farm.world_config["tracks"], seed=seed if seed is not None else farm.seed,
                     base=setup.get("soil_base"))
    area = np.array([r.length * r.width / 1e4 for r in rows])
    polys = [Polygon(r.polygon) for r in rows]
    nb = section_neighbours(polys)
    import scipy.sparse as sp
    ii = [i for i, js in enumerate(nb) for _ in js]
    jj = [j for js in nb for j in js]
    W = sp.csr_matrix((np.ones(len(ii)), (ii, jj)), shape=(len(rows), len(rows)))
    deg = np.asarray(W.sum(axis=1)).ravel()
    W = sp.diags(1 / np.maximum(deg, 1)) @ W
    crops = crop_keys or [r.crop if r.crop in CROPS else DEFAULT_CROP for r in rows]
    model = FieldModel(soil.sec, area, crops, neighbours=W, seed=seed if seed is not None else farm.seed)
    return rows, soil, model, nb


def _lerp(a, b, t):
    t = float(np.clip(t, 0, 1))
    return tuple(x + (y - x) * t for x, y in zip(a, b))


SOIL_DRY, SOIL_WET, POND = (0.46, 0.34, 0.2), (0.3, 0.22, 0.13), (0.3, 0.37, 0.44)
GREEN, STRESSED, DISEASED = (0.2, 0.55, 0.17), (0.58, 0.6, 0.2), (0.45, 0.36, 0.18)
RIPE_RGB = {"maize": (0.86, 0.74, 0.34), "wheat": (0.9, 0.78, 0.4), "soybean": (0.66, 0.52, 0.28), "tomato": (0.8, 0.2, 0.14)}


class WorldEngine:
    def __init__(self) -> None:
        self.farm_id: str | None = None
        self.ready = False
        self.task: asyncio.Task | None = None
        self.day_seconds = 30.0
        self.paused = False
        self.scenarios: list[dict] = []
        self.forecast: dict | None = None
        self.outlook: list[dict] = []
        self._sent: dict[str, tuple] = {}
        self._lock = asyncio.Lock()
        self._forecasting = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._loop(), name="world-engine")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            self.task = None
        if self.ready:
            await self.snapshot()

    async def load(self, farm_id: str | None, fresh: bool = False) -> None:
        """(Re)load the world for the active farm. `fresh` starts a new season (crop plan or layout
        changed): the old snapshot is discarded and the farm's section records are reset."""
        async with self._lock:
            if self.ready and self.farm_id and not (fresh and self.farm_id == farm_id):
                await self.snapshot()
            self.ready = False
            self.farm_id = None
            if not farm_id:
                return
            async with session_scope() as s:
                farm = await s.get(Farm, farm_id)
                if farm is None or not farm.world_config or not (farm.setup or {}).get("climate"):
                    log.info("world engine idle: farm %s has no site analysis yet", farm_id)
                    return
                rows = (await s.execute(select(FieldSection).where(FieldSection.farm_id == farm_id))).scalars().all()
                snap = await s.get(WorldState, farm_id)
                if fresh and snap is not None:
                    await s.delete(snap)
                    snap = None
                if fresh:
                    for r in rows:
                        r.state, r.weeds, r.ndvi, r.cwsi, r.disease, r.weed_cover, r.yield_est = "fallow", False, None, None, None, None, None
                        r.last_survey_at = None
                for r in rows:
                    s.expunge(r)
                s.expunge(farm)
                if snap is not None:
                    s.expunge(snap)
            if not rows:
                return
            plan = farm.crop_plan or {}
            crop_of = plan.get("sections", {})
            crop_keys_by_id = {r.id: crop_of.get(r.id, plan.get("crop", r.crop if r.crop in CROPS else DEFAULT_CROP)) for r in rows}
            rows, soil, model, nb = build_world(farm, rows)
            model.set_crops([crop_keys_by_id[r.id] for r in rows])
            self.farm, self.rows, self.soil, self.model, self.nb = farm, rows, soil, model, nb
            self.ids = [r.id for r in rows]
            self.idx = {sid: i for i, sid in enumerate(self.ids)}
            self.area = model.area
            self.climate = farm.setup["climate"]
            self.lat, self.lon = farm.centroid_lat, farm.centroid_lng
            self.gen = WeatherGenerator(self.climate, 1, farm.seed + 17)
            self.det = AnomalyDetector(self.climate, self.ids, nb)
            # bed-by-bed sweep for machines: by bed, then along the bed
            order = sorted(range(len(rows)), key=lambda i: (len(rows[i].bed_id), rows[i].bed_id, rows[i].id))
            self.ops = Operations(self.area, order, model.crop_keys, self.lat)
            self.probes = np.array(sorted(set(np.linspace(0, len(rows) - 1, min(6, len(rows))).astype(int).tolist())))
            self.obs: dict[int, dict] = {}
            self.rng = np.random.default_rng(farm.seed + 5)
            revision = float(farm.world_config["farm"].get("revision", 0))
            restored = False
            if snap is not None and snap.extra.get("revision") == revision and snap.extra.get("n") == len(rows):
                try:
                    model.load(snap.model)
                    self.gen.load_state(snap.weather)
                    self.sim_date = date.fromisoformat(snap.sim_date)
                    self.frac = float(snap.sim_day_frac)
                    self.day_seconds = float(snap.day_seconds)
                    self.paused = bool(snap.paused)
                    ex = snap.extra
                    self.scenarios = ex.get("scenarios", [])
                    self.obs = {int(k): v for k, v in ex.get("obs", {}).items()}
                    self.ops.load(ex.get("ops", {}))
                    d = ex.get("det", {})
                    self.det.rain30.extend(d.get("rain30", []))
                    self.det.heat_run, self.det.cusum = d.get("heat_run", 0), d.get("cusum", 0.0)
                    self.det.active = d.get("active", {})
                    self.forecast = ex.get("forecast")
                    restored = True
                except Exception:  # noqa: BLE001
                    log.exception("world snapshot unusable - starting a fresh season")
            if not restored:
                start = plan.get("start_date")
                self.sim_date = date.fromisoformat(start) if start else self._season_start()
                if fresh:  # records of a discarded season dated from here on would read as history
                    from sqlalchemy import delete as _delete
                    async with session_scope() as s:
                        for tbl in (WeatherDaily, FarmDaily):
                            await s.execute(_delete(tbl).where(tbl.farm_id == farm_id, tbl.date >= self.sim_date.isoformat()))
                self.frac = 0.25
                self.scenarios = []
                self.forecast = None
            self.revision = revision
            self.today = self.gen.step(self.sim_date, self.scenarios)
            self.summary = model.daily_summary(self.today, np.zeros((1, model.n)))
            self.ops.plan(model, self.sim_date, self.obs, nb, float(self.today.rain[0]))
            self._sent = {}
            self.farm_id = farm_id
            self.ready = True
            await self._write_soil()
            log.info("world model live for %s: %d sections, %s, start %s (%s)", farm.name, len(rows),
                     self.climate.get("source"), self.sim_date, "restored" if restored else "new season")

    def _season_start(self) -> date:
        """A week before the main crop's next sowing window (from today's real date)."""
        crop = (self.farm.crop_plan or {}).get("crop", DEFAULT_CROP)
        sow = fc.next_sowing(crop, self.lat, date.today())
        return max(date.today(), sow - timedelta(days=7))

    # ------------------------------------------------------------------ main loop
    async def _loop(self) -> None:
        last = time.monotonic()
        t_push = t_wx = 0.0
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            dt, last = now - last, now
            if not self.ready:
                continue
            try:
                async with self._lock:
                    if not self.ready:
                        continue
                    if not self.paused:
                        step = dt / self.day_seconds
                        self.frac += step
                        await self._virtual_ops(step)
                        while self.frac >= 1.0:
                            self.frac -= 1.0
                            await self._end_of_day()
                    t_push += dt
                    t_wx += dt
                    if t_push >= 2.0:
                        t_push = 0.0
                        await self._push_field()
                        await self._push_world()
                    if t_wx >= 5.0:
                        t_wx = 0.0
                        await self._push_weather()
            except Exception:  # noqa: BLE001
                log.exception("world engine tick failed")

    # ------------------------------------------------------------------ day boundary
    async def _end_of_day(self) -> None:
        m, day = self.model, self.today
        stage_before = m.s["stage"][0].copy()
        ip = m.irrigation
        ip["failed"] = self._active("irrigation_failure")
        TAW = m.taw()[0]
        crop_on = (stage_before >= SOWN) & (stage_before <= GRAIN_FILL)
        demand = float((np.where(crop_on & (m.s["dr"][0] > ip["mad"] * TAW), np.minimum(m.s["dr"][0], ip["capacity_mm"]), 0) * self.area).sum() / self.area.sum())
        irr_before = m.s["irrigated_mm"][0].copy()
        summary = m.step(day)
        delivered = float(((m.s["irrigated_mm"][0] - irr_before) * ip["efficiency"] * self.area).sum() / self.area.sum())
        summary.update({"irr_demand_mm": round(demand, 2), "irr_delivered_mm": round(delivered, 2)})
        self.summary = summary
        changed = np.flatnonzero(m.s["stage"][0] != stage_before)
        crop = m.crop_at(0)
        sensitive = bool(((m.s["stage"][0] >= VEGETATIVE) & (m.s["stage"][0] <= GRAIN_FILL)).any())
        probes = probe_readings(m, self.probes, self.rng)
        for o in self.obs.values():
            o["age_days"] = self.sim_date.toordinal() - o["day"]
        new_anoms, cleared = self.det.update(day, crop_sensitive=sensitive, frost_kill=crop.frost_kill, obs=self.obs, probes=probes,
                                             irrigation_demand_mm=demand, irrigation_delivered_mm=delivered)
        wx = day.at()
        wx["tmean"] = round((wx["tmax"] + wx["tmin"]) / 2, 2)
        wx["probes"] = probes
        d_iso = self.sim_date.isoformat()
        async with session_scope() as s:
            await s.merge(WeatherDaily(farm_id=self.farm_id, date=d_iso, data=wx))
            await s.merge(FarmDaily(farm_id=self.farm_id, date=d_iso, data=summary))
            for a in new_anoms:
                row = (await s.execute(select(Anomaly).where(Anomaly.farm_id == self.farm_id, Anomaly.key == a["key"],
                                                             Anomaly.active.is_(True)))).scalar_one_or_none()
                if row is None:
                    s.add(Anomaly(farm_id=self.farm_id, key=a["key"], kind=a["kind"], severity=a["severity"], title=a["title"],
                                  data=a, started=a["since"]))
                else:
                    row.title, row.severity, row.data = a["title"], a["severity"], a
            for k in cleared:
                for row in (await s.execute(select(Anomaly).where(Anomaly.farm_id == self.farm_id, Anomaly.key == k,
                                                                  Anomaly.active.is_(True)))).scalars():
                    row.active, row.ended = False, d_iso
            if len(changed):
                for r in (await s.execute(select(FieldSection).where(FieldSection.farm_id == self.farm_id,
                                                                     FieldSection.id.in_([self.ids[i] for i in changed])))).scalars():
                    r.state = STAGE_NAMES[int(m.s["stage"][0, self.idx[r.id]])]
        for a in new_anoms:
            await fleet.publish_dashboard({"t": "anomaly", "anomaly": a, "date": d_iso})
        for k in cleared:
            await fleet.publish_dashboard({"t": "anomaly_cleared", "key": k, "date": d_iso})
        for i in changed:
            await fleet.publish_dashboard({"t": "section", "id": self.ids[i], "state": STAGE_NAMES[int(m.s["stage"][0, i])]})

        # next day
        self.sim_date += timedelta(days=1)
        self._apply_model_scenarios()
        self.today = self.gen.step(self.sim_date, self.scenarios)
        self.ops.plan(m, self.sim_date, self.obs, self.nb, float(self.today.rain[0]))
        await self.snapshot()
        if self.sim_date.toordinal() % 7 == 0 or self.forecast is None:
            asyncio.create_task(self.refresh_forecast())
        else:
            asyncio.create_task(self._refresh_outlook())
        await fleet.publish_dashboard({"t": "world_day", "date": d_iso, "summary": summary, "weather": wx})

    def _active(self, kind: str) -> bool:
        o = self.sim_date.toordinal()
        return any(s["kind"] == kind and s["start"] <= o < s["start"] + s["days"] for s in self.scenarios)

    def _apply_model_scenarios(self) -> None:
        o = self.sim_date.toordinal()
        for sc in self.scenarios:
            if sc["kind"] == "pest_outbreak" and sc["start"] == o:
                m = self.model
                canopy = np.flatnonzero((m.s["stage"][0] >= VEGETATIVE) & (m.s["stage"][0] <= GRAIN_FILL))
                if len(canopy):
                    k = max(1, int(len(canopy) * 0.02 * sc.get("intensity", 1.0)))
                    seeds = self.rng.choice(canopy, size=min(k, len(canopy)), replace=False)
                    m.s["disease"][0, seeds] = np.maximum(m.s["disease"][0, seeds], 0.06)
        self.scenarios = [s for s in self.scenarios if o < s["start"] + s["days"] + 1]

    # ------------------------------------------------------------------ operations
    async def _virtual_ops(self, frac: float) -> None:
        claims = await self._claims()
        claimed = {self.idx[sid] for sid in claims if sid in self.idx}
        for kind, idx in self.ops.tick(frac, claimed):
            await self.apply_work(kind, idx, robot_id=None)

    async def _claims(self) -> dict:
        r = get_redis()
        raw = await r.hgetall(KEY_CLAIMS)
        now = time.time()
        out, stale = {}, []
        for k, v in raw.items():
            k = k.decode() if isinstance(k, bytes) else k
            try:
                c = json.loads(v)
            except (TypeError, json.JSONDecodeError):
                stale.append(k)
                continue
            if now - c.get("ts", 0) > CLAIM_TTL_S:
                stale.append(k)
            else:
                out[k] = c
        if stale:
            await r.hdel(KEY_CLAIMS, *stale)
        return out

    async def apply_work(self, kind: str, idx: list[int], robot_id: str | None, mission_id: str | None = None) -> list[dict]:
        """Apply a field operation to the truth and book it. `robot_id` None = the virtual fleet."""
        m = self.model
        idx = [i for i in idx if 0 <= i < m.n]
        if not idx:
            return []
        results = []
        if kind == "seed":
            m.sow(idx)
            for i in idx:  # starter nitrogen with the drill (40 % of the crop's rate)
                m.fertilise([i], 0.4 * N_RATE.get(m.crop_keys[i], 0))
        elif kind == "fertilise":
            for i in idx:
                m.fertilise([i], 0.6 * N_RATE.get(m.crop_keys[i], 0))
                self.ops.sidedressed[i] = True
        elif kind == "weed":
            m.weed_control(idx)
        elif kind == "spray":
            m.spray(idx)
        elif kind == "harvest":
            before, y = m.harvest(idx)
            for j, i in enumerate(idx):
                kg = float(y[j] * self.area[i] * 1000)
                results.append({"i": i, "was": STAGE_NAMES[int(before[j])], "kg": round(kg, 1)})
        if robot_id:
            for i in idx:
                self.ops.complete(kind, i)
            await get_redis().hdel(KEY_CLAIMS, *[self.ids[i] for i in idx])
        stage = m.s["stage"][0]
        async with session_scope() as s:
            rows = {r.id: r for r in (await s.execute(select(FieldSection).where(FieldSection.farm_id == self.farm_id,
                                                                                    FieldSection.id.in_([self.ids[i] for i in idx])))).scalars()}
            for i in idx:
                r = rows.get(self.ids[i])
                if r is None:
                    continue
                r.state = STAGE_NAMES[int(stage[i])]
                if kind == "weed":
                    r.weeds, r.weed_cover = False, 0.0
                if kind == "spray":
                    r.disease = 0.0
                if kind in ("seed", "harvest"):
                    r.weeds, r.weed_cover, r.disease, r.yield_est = False, 0.0, 0.0, None
            for res in results:
                if res["kg"] > 0:
                    s.add(YieldRecord(farm_id=self.farm_id, section_id=self.ids[res["i"]], crop_type=m.crop_keys[res["i"]],
                                      kg=res["kg"], robot_id=robot_id or "fleet"))
        for i in idx:
            self.obs.pop(i, None) if kind in ("seed", "harvest") else None
            if kind in ("weed", "spray") and i in self.obs:
                self.obs[i]["weeds" if kind == "weed" else "disease"] = False if kind == "weed" else 0.0
        msgs = []
        res_by_i = {r["i"]: r for r in results}
        for i in idx:
            msg = {"t": "section", "id": self.ids[i], "state": STAGE_NAMES[int(stage[i])], "by": robot_id or "fleet", "op": kind}
            if kind == "weed":
                msg["weeds"] = False
            if i in res_by_i:
                msg.update(kg=res_by_i[i]["kg"], was=res_by_i[i]["was"])
            msgs.append(msg)
        if robot_id or len(msgs) <= 3:
            for msg in msgs:
                await fleet.publish_dashboard(msg)
        else:
            await fleet.publish_dashboard({"t": "sections", "items": msgs, "op": kind, "by": "fleet"})
        if results:
            tot = sum(r["kg"] for r in results)
            log.info("%s harvested %d section(s): %.0f kg (%s)", robot_id or "fleet", len(results), tot,
                     ", ".join(sorted({r["was"] for r in results})))
        return results

    async def on_work_event(self, event_kind: str, section_id: str, robot_id: str | None, mission_id: str | None) -> list[dict]:
        kind = KIND_OF_EVENT.get(event_kind)
        if not self.ready or kind is None or section_id not in self.idx:
            return []
        async with self._lock:
            return await self.apply_work(kind, [self.idx[section_id]], robot_id, mission_id)

    async def on_section_seen(self, section_id: str, robot_id: str | None) -> None:
        if not self.ready or section_id not in self.idx:
            return
        i = self.idx[section_id]
        o = uav_observe(self.model, np.array([i]), self.rng)[0]
        o["day"] = self.sim_date.toordinal()
        o["age_days"] = 0
        self.obs[i] = o
        async with session_scope() as s:
            r = (await s.execute(select(FieldSection).where(FieldSection.farm_id == self.farm_id, FieldSection.id == section_id))).scalar_one_or_none()
            if r is not None:
                r.ndvi, r.cwsi, r.disease, r.weed_cover = o["ndvi"], o["cwsi"], o["disease"], o["weed_cover"]
                r.weeds, r.moisture, r.health = o["weeds"], min(1.0, o["moisture"]), o["health"]
                r.yield_est = o["yield_est_t_ha"] if o["stage"] in ("flowering", "grain_fill", "ripe") else None
                r.last_survey_at = datetime.now(timezone.utc)
        await fleet.publish_dashboard({"t": "section", "id": section_id, "surveyed": True, "ndvi": o["ndvi"], "cwsi": o["cwsi"],
                                       "weeds": o["weeds"], "disease": o["disease"], "moisture": min(1.0, o["moisture"]),
                                       "health": o["health"], "yield_est": o["yield_est_t_ha"], "by": robot_id})

    # ------------------------------------------------------------------ outputs
    def hour(self) -> float:
        return self.frac * 24.0

    def now_weather(self) -> dict:
        d, h = self.today, self.hour()
        irr = irradiance_wm2(d, self.lat, h)
        return {"t": round(hourly_temperature(d, h), 1), "irradiance_wm2": round(irr), "rh": round(float(d.rh[0])),
                "wind": round(float(d.wind[0]), 1), "rain_today": round(float(d.rain[0]), 1), "cloud": round(float(d.cloud[0]), 2),
                "et0": round(float(d.et0[0]), 2), "tmax": round(float(d.tmax[0]), 1), "tmin": round(float(d.tmin[0]), 1),
                "hail": bool(d.hail[0]), "scenarios": d.scenarios}

    def clock(self) -> dict:
        crop = self.model.crop_at(0)
        return {"date": self.sim_date.isoformat(), "hour": round(self.hour(), 2), "doy": self.today.doy, "day_seconds": self.day_seconds,
                "paused": self.paused, "crop": crop.key, "crop_name": crop.name,
                "sow_months": list(sow_months_for(crop, self.lat))}

    async def _push_world(self) -> None:
        m = self.model
        st = m.s["stage"][0]
        msg = {"t": "world", "farm_id": self.farm_id, "clock": self.clock(), "weather": self.now_weather(), "kpi": self.summary,
               "ops": self.ops.summary(), "anomalies": list(self.det.active.values()),
               "forecast": {k: self.forecast.get(k) for k in ("yield_t", "yield_t_ha", "revenue_usd", "maturity_date", "planted_ha")}
               if self.forecast and self.forecast.get("planted_sections") else None,
               "stages": {STAGE_NAMES[k]: int((st == k).sum()) for k in range(7)}}
        await fleet.publish_dashboard(msg)
        r = get_redis()
        await r.set(KEY_NOW, json.dumps({"hour": self.hour(), "irradiance": min(1.1, self.now_weather()["irradiance_wm2"] / 1000),
                                         "wind": float(self.today.wind[0]) * 1.3, "date": self.sim_date.isoformat()}))
        pend = {k: [self.ids[i] for i in v[:60]] for k, v in self.ops.pending.items()}
        await r.set(KEY_OPS, json.dumps({"farm_id": self.farm_id, "pending": pend, "workable": self.ops.workable,
                                         "harvest_ok": getattr(self.ops, "harvest_ok", True)}))

    def _visual(self, i: int, moist: float) -> tuple:
        """Rendered state of section i: height, colour and weed marker (moisture precomputed)."""
        m = self.model
        s = m.s
        st = int(s["stage"][0, i])
        soil = POND if s["pond"][0, i] > 3 else _lerp(SOIL_DRY, SOIL_WET, moist)
        h = float(s["height"][0, i])
        weeds = bool(s["weed"][0, i] > 0.15)
        crop = m.crop_keys[i]
        if st == FALLOW:
            rgb, h = soil, 0.05
        elif st == SOWN:
            rgb, h = _lerp(soil, GREEN, 0.15), 0.06
        elif st == STUBBLE:
            rgb, h = (0.72, 0.62, 0.42), 0.15
        elif st == RIPE:
            rgb = RIPE_RGB.get(crop, (0.85, 0.75, 0.4))
        else:
            cover = 1 - math.exp(-m.c["k_ext"][i] * s["lai"][0, i] * (1 - s["disease"][0, i]))
            rgb = _lerp(soil, GREEN, cover * 1.15)
            rgb = _lerp(rgb, STRESSED, (1 - float(s["ks"][0, i])) * 0.8)
            rgb = _lerp(rgb, DISEASED, float(s["disease"][0, i]) * 1.5)
            if st == GRAIN_FILL:
                prog = (s["gdd"][0, i] - m.c["gdd_flowering"][i]) / max(1.0, m.c["gdd_maturity"][i] - m.c["gdd_flowering"][i])
                rgb = _lerp(rgb, RIPE_RGB.get(crop, (0.85, 0.75, 0.4)), prog * 0.9)
            if st == FLOWERING and crop == "maize":
                rgb = _lerp(rgb, (0.75, 0.7, 0.35), 0.15)
        return (round(max(0.05, h), 2), round(rgb[0], 2), round(rgb[1], 2), round(rgb[2], 2), int(weeds))

    async def _push_field(self) -> None:
        delta = {}
        moist = np.clip(self.model.moisture()[0], 0, 1.5)  # once for the whole field
        for i, sid in enumerate(self.ids):
            v = self._visual(i, float(moist[i]))
            if self._sent.get(sid) != v:
                delta[sid] = list(v)
                self._sent[sid] = v
        if delta:
            await fleet.send_command(Command(kind="field_state", payload={"s": delta}))

    async def _push_weather(self) -> None:
        d, h = self.today, self.hour()
        elev = solar_elevation(self.lat, d.doy, h)
        decl = 0.409 * math.sin(2 * math.pi * d.doy / 365 - 1.39)
        ha = math.radians(15 * (h - 12))
        phi = math.radians(self.lat)
        az = math.atan2(math.sin(ha), math.cos(ha) * math.sin(phi) - math.tan(decl) * math.cos(phi)) + math.pi
        cloud = float(d.cloud[0])
        irr = irradiance_wm2(d, self.lat, h)
        day_f = float(np.clip(math.sin(max(elev, 0)) * 2.2, 0, 1))
        sky = _lerp((0.07, 0.09, 0.16), _lerp((0.62, 0.78, 0.93), (0.6, 0.63, 0.66), cloud), day_f)
        raining = float(d.rain[0]) > 1
        payload = {"sun_elev": round(max(elev, 0.03), 3), "sun_az": round(az, 3), "light": round(0.25 + 1.75 * min(1.0, irr / 900), 2),
                   "sky": [round(x, 3) for x in sky], "luminosity": round(0.25 + 0.75 * day_f, 2),
                   "fog_m": 220.0 if (raining and float(d.rain[0]) > 10) else (0.0 if not raining else 600.0),
                   "wet_tracks": bool(float(d.rain[0]) > 6 or (self.summary or {}).get("waterlogged_pct", 0) > 5),
                   "hour": round(h, 2)}
        await fleet.send_command(Command(kind="weather", payload=payload))
        await fleet.send_command(Command(kind="set_irradiance", payload={"irradiance": round(min(1.1, irr / 1000), 3), "sim_hour": round(h, 2)}))

    async def refresh_forecast(self) -> None:
        if self._forecasting or not self.ready:
            return
        self._forecasting = True
        try:
            m1, g1 = self.model.fork(1, 3), self.gen.fork(1, 4)
            day, sc = self.sim_date, list(self.scenarios)
            self.forecast = await asyncio.to_thread(fc.season, m1, g1, day, sc, 30, 260, int(time.time()) % 10000)
            self.outlook = await asyncio.to_thread(fc.weather_outlook, g1, day, sc)
            if self.forecast.get("planted_sections"):
                await fleet.publish_dashboard({"t": "forecast", "forecast": {k: v for k, v in self.forecast.items()
                                                                              if k not in ("section_yield_t_ha", "section_maturity_day")}})
        except Exception:  # noqa: BLE001
            log.exception("forecast failed")
        finally:
            self._forecasting = False

    async def _refresh_outlook(self) -> None:
        try:
            g1 = self.gen.fork(1, 4)
            self.outlook = await asyncio.to_thread(fc.weather_outlook, g1, self.sim_date, list(self.scenarios))
        except Exception:  # noqa: BLE001
            log.exception("outlook failed")

    async def snapshot(self) -> None:
        if not self.farm_id:
            return
        d = self.det
        extra = {"revision": self.revision, "n": self.model.n, "scenarios": self.scenarios,
                 "obs": {str(k): v for k, v in self.obs.items()}, "ops": self.ops.state(),
                 "det": {"rain30": list(d.rain30), "heat_run": d.heat_run, "cusum": d.cusum, "active": d.active},
                 "forecast": {k: v for k, v in (self.forecast or {}).items()}}
        async with session_scope() as s:
            await s.merge(WorldState(farm_id=self.farm_id, sim_date=self.sim_date.isoformat(), sim_day_frac=self.frac,
                                     day_seconds=self.day_seconds, paused=self.paused, model=self.model.dump(),
                                     weather=self.gen.state(), extra=extra))

    async def _write_soil(self) -> None:
        sec = self.soil.sec
        async with session_scope() as s:
            rows = {r.id: r for r in (await s.execute(select(FieldSection).where(FieldSection.farm_id == self.farm_id))).scalars()}
            for i, sid in enumerate(self.ids):
                r = rows.get(sid)
                if r is not None:
                    r.soil = {"clay": round(float(sec["clay"][i]), 1), "sand": round(float(sec["sand"][i]), 1), "om": round(float(sec["om"][i]), 2),
                              "ph": round(float(sec["ph"][i]), 2), "ec": round(float(sec["ec"][i]), 2), "twi": round(float(sec["twi"][i]), 2),
                              "taw_mm_m": round(float((sec["fc"][i] - sec["wp"][i]) * 1000), 1), "ksat": round(float(sec["ksat"][i]), 1),
                              "elev": round(float(sec["dem"][i]), 2)}

    # ------------------------------------------------------------------ control
    async def set_clock(self, day_seconds: float | None = None, paused: bool | None = None) -> None:
        if day_seconds is not None:
            self.day_seconds = float(np.clip(day_seconds, 3.0, 3600.0))
        if paused is not None:
            self.paused = bool(paused)
        if self.ready:
            await self._push_world()

    async def add_scenario(self, kind: str, days: int, intensity: float, start_in_days: int = 0) -> dict:
        sc = {"id": f"{kind}-{int(time.time())}", "kind": kind, "start": self.sim_date.toordinal() + max(0, start_in_days),
              "days": int(days), "intensity": float(intensity), "label": ALL_SCENARIOS.get(kind, kind)}
        async with self._lock:
            self.scenarios.append(sc)
            if start_in_days == 0:  # today's weather reflects the scenario right away
                self.today = self.gen.step(self.sim_date, self.scenarios)
                self._apply_model_scenarios()
        await self._push_world()
        return sc

    async def clear_scenarios(self) -> None:
        self.scenarios = []
        await self._push_world()

    def truth(self) -> list[dict]:
        m = self.model
        s = m.s
        ndvi, moist, health, ypot = m.ndvi()[0], m.moisture()[0], m.health()[0], m.yield_potential()[0]
        return [{"id": sid, "crop": m.crop_keys[i], "stage": STAGE_NAMES[int(s["stage"][0, i])], "gdd": round(float(s["gdd"][0, i])),
                 "lai": round(float(s["lai"][0, i]), 2), "biomass_t_ha": round(float(s["biomass"][0, i]), 2),
                 "yield_pot_t_ha": round(float(ypot[i]), 2), "ndvi": round(float(ndvi[i]), 3), "moisture": round(float(moist[i]), 3),
                 "ks": round(float(s["ks"][0, i]), 3), "n_soil": round(float(s["n_soil"][0, i]), 1), "weed": round(float(s["weed"][0, i]), 3),
                 "disease": round(float(s["disease"][0, i]), 3), "waterlogged_days": int(s["wl_days"][0, i]), "health": round(float(health[i]), 3),
                 "height": round(float(s["height"][0, i]), 2)} for i, sid in enumerate(self.ids)]


world = WorldEngine()
