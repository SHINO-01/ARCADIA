/** World model dashboard: the farm as a living simulation - weather, soil water, crop, operations, risk. */
import { useEffect, useMemo, useState } from 'react'
import { clsx } from 'clsx'
import { AlertTriangle, Bug, Pause, Play, CalendarDays, FastForward, RotateCcw, CloudRain, Droplets, FlaskConical, Gauge, Leaf, Snowflake, Sprout, Sun, Thermometer, Tractor, Wind, Zap, DropletOff, RefreshCw, X } from 'lucide-react'
import { Card, Chip, Empty, Kpi, Segmented, Toggle } from '../components/ui'
import { FanBar, Legend, StackBar, TimeChart } from '../components/charts'
import { OP_LABEL, SCENARIOS, STAGE_COLORS, STAGE_LABEL, fmtDate, worldApi, type DayWeather, type Kpi as KpiT, type Outlook, type WorldMode, type WorldState } from '../lib/world'
import { useWorld } from '../store/world'
import { useUI } from '../store/ui'

const SCN_ICON: Record<string, typeof Sun> = { drought: Sun, heatwave: Thermometer, wet_spell: CloudRain, frost: Snowflake, storm: Zap, pest_outbreak: Bug, irrigation_failure: DropletOff }

export function WorldView() {
  const w = useWorld()
  const notify = useUI((s) => s.notify)
  const [full, setFull] = useState<WorldState | null>(null)
  const [history, setHistory] = useState<DayWeather[]>([])
  const [outlook, setOutlook] = useState<Outlook[]>([])
  const [daily, setDaily] = useState<(KpiT & { date: string })[]>([])
  const mode = useWorld((s) => s.clock?.mode)
  useEffect(() => {
    const load = () => {
      worldApi.state().then((s) => { setFull(s); if (s.ready) useWorld.getState().setState(s) }).catch(() => {})
      worldApi.weather(45).then((r) => { setHistory(r.history); setOutlook(r.outlook) }).catch(() => {})
      worldApi.daily(240).then(setDaily).catch(() => {})
    }
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [mode])
  // live days pushed between reloads
  const days = useMemo(() => {
    const seen = new Set(daily.map((d) => d.date))
    return [...daily, ...w.days.filter((d) => !seen.has(d.date)).map((d) => ({ ...d.kpi, date: d.date }))]
  }, [daily, w.days])
  const wx = useMemo(() => {
    const seen = new Set(history.map((d) => d.date))
    return [...history, ...w.days.filter((d) => !seen.has(d.date)).map((d) => ({ ...d.weather, date: d.date }))].slice(-45)
  }, [history, w.days])

  if (!w.ready && !full?.ready) return (
    <div className="h-full p-3"><div className="panel h-full"><Empty icon={<Sprout size={28} />} title="The world model starts when a farm goes live">
      Draw a field, run the site analysis and activate the farm. Weather, soil, water and crop growth are then simulated day by day.</Empty></div></div>
  )
  const k = w.kpi
  const c = w.clock
  const stages = w.stages
  const sown = Object.entries(stages).filter(([s]) => !['fallow', 'harvested'].includes(s)).reduce((a, [, v]) => a + v, 0)
  const total = Object.values(stages).reduce((a, v) => a + v, 0)
  const f = w.forecast
  return (
    <div className="scroll h-full overflow-auto p-3">
      <div className="mx-auto grid max-w-[1700px] gap-3" style={{ gridTemplateColumns: 'repeat(12, minmax(0, 1fr))' }}>
        {/* season */}
        <Card className="col-span-12 xl:col-span-5" title={<span>{c?.crop_name ?? 'Season'} <span className="font-normal text-fg-3">· {fmtDate(c?.date, true)}</span></span>} icon={<Sprout size={15} />}
          right={<Chip tone={sown ? 'ok' : 'neutral'}>{sown ? `${Math.round((100 * sown) / Math.max(1, total))}% planted` : 'awaiting sowing'}</Chip>}>
          <StackBar parts={Object.keys(STAGE_LABEL).map((s) => ({ label: STAGE_LABEL[s], value: stages[s] ?? 0, color: STAGE_COLORS[s] }))} />
          <div className="mt-4 grid grid-cols-4 gap-3">
            <Metric label="Canopy LAI" value={k?.lai.toFixed(2)} hint="m² leaf / m² ground" />
            <Metric label="NDVI" value={k?.ndvi.toFixed(2)} hint="field mean, model" />
            <Metric label="Biomass" value={k?.biomass_t_ha.toFixed(1)} unit="t/ha" />
            <Metric label="Soil N" value={k ? Math.round(k.n_soil) : '—'} unit="kg/ha" />
          </div>
          <p className="mt-3 text-[11px] text-fg-3">Sowing window {c?.sow_months.map((m) => ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][m - 1]).join('–')} · climate {full?.climate?.source ?? '—'} · soil {full?.soil?.texture ?? '—'} ({full?.soil?.source ?? '—'})</p>
        </Card>
        {/* weather now */}
        <Card className="col-span-6 xl:col-span-3" title="Weather now" icon={<Sun size={15} />} right={w.weather?.scenarios.map((s) => <Chip key={s} tone="warn">{s.replace('_', ' ')}</Chip>)}>
          <div className="flex items-end gap-3">
            <div className="num text-4xl font-semibold leading-none">{w.weather?.t.toFixed(1)}<span className="text-lg text-fg-3">°C</span></div>
            <div className="pb-0.5 text-[11px] text-fg-3">today {w.weather?.tmin.toFixed(0)}° / {w.weather?.tmax.toFixed(0)}°</div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
            <Row icon={<Sun size={13} />} label="Sun" value={`${w.weather?.irradiance_wm2 ?? 0} W/m²`} />
            <Row icon={<CloudRain size={13} />} label="Rain today" value={`${w.weather?.rain_today ?? 0} mm`} />
            <Row icon={<Wind size={13} />} label="Wind" value={`${w.weather?.wind ?? 0} m/s`} />
            <Row icon={<Droplets size={13} />} label="Humidity" value={`${w.weather?.rh ?? 0}%`} />
            <Row icon={<Gauge size={13} />} label="ET₀" value={`${w.weather?.et0 ?? 0} mm/d`} />
            <Row icon={<CloudRain size={13} />} label="Cloud" value={`${Math.round((w.weather?.cloud ?? 0) * 100)}%`} />
          </div>
        </Card>
        {/* forecast */}
        <Card className="col-span-6 xl:col-span-4" title="Season outcome" icon={<CalendarDays size={15} />}
          right={<button className="btn btn-ghost" title="Re-run the Monte Carlo forecast" onClick={() => worldApi.refreshForecast().then(() => notify('Forecast updated', 'ok')).catch((e) => notify(e.message, 'err'))}><RefreshCw size={12} /></button>}>
          {f?.yield_t ? (<>
            <div className="label mb-1.5">Harvest forecast · {f.members} weather futures</div>
            <FanBar q={f.yield_t} max={f.yield_t.p90 * 1.15} unit="t" />
            <div className="mt-3 grid grid-cols-3 gap-3">
              <Metric label="Yield" value={f.yield_t_ha?.p50.toFixed(1)} unit="t/ha" />
              <Metric label="Revenue" value={f.revenue_usd ? `$${Math.round(f.revenue_usd.p50 / 1000)}k` : '—'} hint={f.revenue_usd ? `P10 $${Math.round(f.revenue_usd.p10 / 1000)}k` : ''} />
              <Metric label="Maturity" value={fmtDate(f.maturity_date?.p50)} hint={f.maturity_date?.p10 ? `${fmtDate(f.maturity_date.p10)} – ${fmtDate(f.maturity_date.p90)}` : ''} />
            </div>
          </>) : <p className="text-xs text-fg-3">The Monte Carlo forecast runs once the crop is in the ground: the rest of the season is simulated under many plausible weather futures.</p>}
        </Card>

        {/* KPIs */}
        <div className="col-span-12 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
          <Kpi label="Root-zone water" value={k ? Math.round(Math.min(1, k.moisture) * 100) : '—'} unit="% of TAW" tone={k && k.moisture < 0.45 ? 'warn' : 'ok'} icon={<Droplets size={11} />} caption={k && k.ks < 0.95 ? `water stress Ks ${k.ks.toFixed(2)}` : 'no water stress'} />
          <Kpi label="Irrigation today" value={k ? Math.round(k.irrigation_m3) : '—'} unit="m³" icon={<Droplets size={11} />} caption={k?.irr_demand_mm != null ? `demand ${k.irr_demand_mm} mm · delivered ${k.irr_delivered_mm} mm` : ''} tone="info" />
          <Kpi label="Weed cover" value={k ? (k.weed * 100).toFixed(1) : '—'} unit="%" icon={<Leaf size={11} />} tone={k && k.weed > 0.1 ? 'warn' : undefined} />
          <Kpi label="Disease" value={k ? (k.disease * 100).toFixed(1) : '—'} unit="% severity" icon={<Bug size={11} />} tone={k && k.disease > 0.05 ? 'bad' : undefined} />
          <Kpi label="Waterlogged" value={k ? k.waterlogged_pct.toFixed(1) : '—'} unit="% of beds" icon={<CloudRain size={11} />} tone={k && k.waterlogged_pct > 2 ? 'warn' : undefined} />
          <Kpi label="Yield potential" value={k ? Math.round(k.yield_pot_t) : '—'} unit="t" icon={<Sprout size={11} />} caption="if harvested at maturity" tone="ok" />
        </div>

        {/* charts */}
        <Card className="col-span-12 xl:col-span-6" title="Weather · last 45 days and 7-day outlook" icon={<CloudRain size={15} />}
          right={<Legend items={[{ label: 'Tmax', color: 'var(--color-rust)' }, { label: 'Tmin', color: 'var(--color-sky)' }, { label: 'Rain', color: '#4a7fd0' }, { label: 'ET₀', color: 'var(--color-sun)', dashed: true }]} />}>
          <WeatherChart wx={wx} outlook={outlook} />
        </Card>
        <Card className="col-span-12 xl:col-span-6" title="Crop and soil water through the season" icon={<Sprout size={15} />}
          right={<Legend items={[{ label: 'NDVI', color: 'var(--color-leaf)' }, { label: 'LAI', color: '#9fc24a', dashed: true }, { label: 'Soil water', color: 'var(--color-sky)' }]} />}>
          <TimeChart height={190} labels={days.map((d) => d.date)} fmtX={(s) => fmtDate(s)} yMin={0}
            series={[
              { key: 'ndvi', label: 'NDVI', values: days.map((d) => d.ndvi), color: 'var(--color-leaf)', kind: 'area' },
              { key: 'moist', label: 'Soil water', values: days.map((d) => Math.min(1.2, d.moisture)), color: 'var(--color-sky)' },
              { key: 'lai', label: 'LAI', values: days.map((d) => d.lai), color: '#9fc24a', axis: 'right', dashed: true },
            ]} emptyText="The season's record builds up day by day" />
        </Card>

        {/* operations */}
        <Card className="col-span-12 lg:col-span-4" title="Field operations" icon={<Tractor size={15} />}
          right={<Chip tone={w.ops?.workable ? 'ok' : 'warn'}>{w.ops?.workable ? 'field workable' : 'too wet to work'}</Chip>}>
          <p className="mb-3 text-[11px] text-fg-3">{w.ops?.units} fleet unit{w.ops?.units === 1 ? '' : 's'} × {w.ops?.hours_per_day} h/day work the campaign queues; the simulated robots claim sections from the same queues.</p>
          <div className="space-y-3">
            {Object.keys(OP_LABEL).map((op) => {
              const pend = w.ops?.pending[op] ?? 0
              const done = w.ops?.season_ha[op] ?? 0
              const pendHa = w.ops?.pending_ha[op] ?? 0
              return (
                <div key={op}>
                  <div className="flex items-baseline justify-between text-xs"><span className="font-medium">{OP_LABEL[op]}</span>
                    <span className="num text-fg-3">{done.toFixed(1)} ha done{pend ? ` · ${pend} queued (${pendHa.toFixed(1)} ha)` : ''}</span></div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-leaf-2 transition-[width] duration-500" style={{ width: `${(done / Math.max(done + pendHa, 1e-6)) * 100}%` }} /></div>
                </div>
              )
            })}
          </div>
        </Card>
        <AnomalyCard />
        <ScenarioLab irrigation={full?.irrigation} />
      </div>
    </div>
  )
}

function Metric({ label, value, unit, hint }: { label: string; value?: string | number; unit?: string; hint?: string }) {
  return <div><div className="label">{label}</div><div className="num mt-0.5 text-lg font-semibold leading-tight">{value ?? '—'}{unit && <span className="ml-1 text-[11px] font-normal text-fg-3">{unit}</span>}</div>{hint && <div className="text-[10px] text-fg-3">{hint}</div>}</div>
}
function Row({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="flex items-center gap-2"><span className="text-fg-3">{icon}</span><span className="text-fg-2">{label}</span><span className="num ml-auto">{value}</span></div>
}

function WeatherChart({ wx, outlook }: { wx: DayWeather[]; outlook: Outlook[] }) {
  const labels = [...wx.map((d) => d.date), ...outlook.map((o) => o.date)]
  const nan = (n: number) => Array(n).fill(null)
  const tmaxBand = { lo: [...wx.map((d) => d.tmax), ...outlook.map((o) => o.tmax.p10)], hi: [...wx.map((d) => d.tmax), ...outlook.map((o) => o.tmax.p90)], color: 'var(--color-rust)', label: 'Tmax P10–P90' }
  return (
    <TimeChart height={190} labels={labels} fmtX={(s) => fmtDate(s)} marker={wx.length - 1} bands={outlook.length ? [tmaxBand] : []}
      series={[
        { key: 'rain', label: 'Rain', unit: 'mm', values: [...wx.map((d) => d.rain), ...outlook.map((o) => o.rain_mm.p50)], color: '#4a7fd0', kind: 'bar', axis: 'right' },
        { key: 'tmax', label: 'Tmax', unit: '°C', values: [...wx.map((d) => d.tmax), ...outlook.map((o) => o.tmax.p50)], color: 'var(--color-rust)' },
        { key: 'tmin', label: 'Tmin', unit: '°C', values: [...wx.map((d) => d.tmin), ...outlook.map((o) => o.tmin.p50)], color: 'var(--color-sky)' },
        { key: 'et0', label: 'ET₀', unit: 'mm', values: [...wx.map((d) => d.et0), ...nan(outlook.length)], color: 'var(--color-sun)', axis: 'right', dashed: true },
      ]} emptyText="Weather history builds up as the simulation runs" />
  )
}

function AnomalyCard() {
  const anomalies = useWorld((s) => s.anomalies)
  const sorted = [...anomalies].sort((a, b) => (b.severity === 'critical' ? 1 : 0) - (a.severity === 'critical' ? 1 : 0))
  return (
    <Card className="col-span-12 lg:col-span-4" title="Anomalies" icon={<AlertTriangle size={15} />} right={<Chip tone={anomalies.length ? 'warn' : 'ok'}>{anomalies.length ? `${anomalies.length} active` : 'all normal'}</Chip>}>
      {!sorted.length && <p className="text-xs text-fg-3">Weather, soil probes, flow meters and UAV imagery are screened at the end of every farm day: climatology z-scores, SPI, CUSUM control charts and spatial NDVI outliers.</p>}
      <ul className="space-y-2.5">
        {sorted.map((a) => (
          <li key={a.key} className={clsx('rounded-lg border-l-2 bg-panel-2/60 px-3 py-2', a.severity === 'critical' ? 'border-alert' : 'border-sun')}>
            <div className="flex items-start gap-2"><span className="text-xs font-semibold">{a.title}</span><span className="num ml-auto shrink-0 text-[10px] text-fg-3">since {fmtDate(a.since)}</span></div>
            <div className="mt-0.5 text-[11px] text-fg-2">{a.detail}</div>
            <div className="mt-1 text-[11px] text-leaf">→ {a.action}</div>
            <div className="mt-1 text-[10px] text-fg-3">{a.method}{a.sections.length ? ` · ${a.sections.length} section${a.sections.length > 1 ? 's' : ''}` : ''}</div>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function ScenarioLab({ irrigation }: { irrigation?: WorldState['irrigation'] }) {
  const notify = useUI((s) => s.notify)
  const sandbox = useWorld((s) => s.clock?.mode === 'simulation')
  const [intensity, setIntensity] = useState(1)
  // local edits override the server value until the next state refresh replaces `irrigation`
  const [edit, setEdit] = useState<{ base: WorldState['irrigation']; value: WorldState['irrigation'] } | null>(null)
  const irr = edit && edit.base === irrigation ? edit.value : irrigation
  const setIrr = (value: WorldState['irrigation']) => setEdit({ base: irrigation, value })
  const run = (kind: string, days: number) => worldApi.addScenario({ kind, days, intensity })
    .then((s) => notify(`${s.label} for ${s.days} day${s.days > 1 ? 's' : ''}`, 'ok')).catch((e) => notify(e.message, 'err'))
  return (
    <Card className="col-span-12 lg:col-span-4" title="Scenario lab" icon={<FlaskConical size={15} />}
      right={sandbox && <button className="btn btn-ghost" title="Clear all scenarios" onClick={() => worldApi.clearScenarios().then(() => notify('Scenarios cleared', 'ok'))}><X size={12} />clear</button>}>
      {sandbox
        ? <p className="mb-2 text-[11px] text-fg-3">Stress-test the sandbox. Scenarios layer on the farm's own climate; the anomaly detectors and the forecast react. The real-time twin is not affected.</p>
        : <div className="mb-2 flex items-start gap-2 rounded-lg border border-line bg-panel-2/50 px-2.5 py-2 text-[11px] text-fg-3">
            <span className="flex-1">The real-time twin follows the farm's actual clock. What-if scenarios run in the simulation sandbox, a copy of the live state you can fast-forward.</span>
            <button className="btn shrink-0" onClick={() => setMode('simulation', notify)}><FlaskConical size={12} className="text-sun" />Open sandbox</button>
          </div>}
      <div className="grid grid-cols-2 gap-1.5">
        {SCENARIOS.map((s) => { const I = SCN_ICON[s.kind] ?? Zap; return (
          <button key={s.kind} className="btn justify-start" disabled={!sandbox} onClick={() => run(s.kind, s.days)}><I size={13} className="text-sun" />{s.label}</button>) })}
      </div>
      <label className={clsx('mt-3 block text-[11px] text-fg-3', !sandbox && 'opacity-50')}>Intensity <span className="num text-fg">{intensity.toFixed(1)}×</span>
        <input type="range" min={0.4} max={2} step={0.1} value={intensity} disabled={!sandbox} onChange={(e) => setIntensity(Number(e.target.value))} className="w-full" /></label>
      {irr && (
        <div className="mt-3 border-t border-line pt-3">
          <div className="flex items-center gap-2 text-xs font-medium"><Droplets size={13} className="text-sky" />Irrigation
            <span className="ml-auto"><Toggle checked={irr.enabled} label="irrigation" onChange={(v) => worldApi.irrigation({ enabled: v }).then(setIrr)} /></span></div>
          <label className="mt-2 block text-[11px] text-fg-3">Trigger at <span className="num text-fg">{Math.round(irr.mad * 100)}%</span> depletion of available water
            <input type="range" min={0.2} max={0.9} step={0.05} value={irr.mad} onChange={(e) => setIrr({ ...irr, mad: Number(e.target.value) })}
              onMouseUp={() => worldApi.irrigation({ mad: irr.mad })} onKeyUp={() => worldApi.irrigation({ mad: irr.mad })} className="w-full" /></label>
          {irr.failed && <Chip tone="bad">pump failure active</Chip>}
        </div>
      )}
    </Card>
  )
}

function setMode(mode: WorldMode, notify: (m: string, tone?: 'ok' | 'err') => void) {
  worldApi.mode(mode).then(() => notify(mode === 'simulation' ? 'Simulation sandbox: the real-time twin keeps its own state' : 'Back to the real-time twin', 'ok'))
    .catch((e) => notify(e.message, 'err'))
}

const hhmm = (h: number) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.floor((h % 1) * 60)).padStart(2, '0')}`

/** World clock for the status bar: Real-time / Simulation switch, the farm date and time, air temperature. */
export function WorldClock() {
  const clock = useWorld((s) => s.clock)
  const weather = useWorld((s) => s.weather)
  const notify = useUI((s) => s.notify)
  if (!clock) return null
  const sim = clock.mode === 'simulation'
  const tz = `UTC${clock.utc_offset_h >= 0 ? '+' : ''}${clock.utc_offset_h.toFixed(1)}`
  return (
    <div className="flex items-center gap-2">
      <Segmented<WorldMode> size="xs" value={clock.mode} onChange={(m) => setMode(m, notify)} options={[
        { value: 'realtime', label: <span className="flex items-center gap-1"><span className={clsx('h-1.5 w-1.5 rounded-full', sim ? 'bg-fg-3' : 'animate-pulse bg-leaf')} />Real-time</span>, title: 'The live twin: farm time runs 1:1 with the wall clock' },
        { value: 'simulation', label: <span className="flex items-center gap-1"><FlaskConical size={10} />Simulation</span>, title: 'What-if sandbox forked from the real-time state: fast-forward, pause, scenarios' },
      ]} />
      <span className={clsx('num w-[148px] whitespace-nowrap rounded-md px-2 py-0.5 text-center text-[11px]', sim ? 'bg-sun/15 text-sun' : 'bg-panel-2 text-fg')}
        title={sim ? 'Sandbox date and time' : `Farm date and local solar time (${tz}), running 1:1 with the wall clock`}>
        {fmtDate(clock.date, true)} · {hhmm(clock.hour)}</span>
      {clock.catching_up && <span className="chip bg-sky/15 text-sky" title="The twin was offline and is stepping through the days it missed">catching up</span>}
      {weather && <span className="num hidden w-[44px] text-[11px] text-fg-2 lg:inline" title={`Air temperature${weather.rain_today > 0.5 ? ` · ${weather.rain_today} mm rain today` : ''}`}>{weather.t.toFixed(0)}°C</span>}
    </div>
  )
}

/** Second bar shown only in simulation mode: the sandbox's own controls, kept out of the status bar. */
export function SandboxBar() {
  const clock = useWorld((s) => s.clock)
  const notify = useUI((s) => s.notify)
  if (clock?.mode !== 'simulation') return null
  const speeds = [{ value: '120', label: '1 d / 2 min' }, { value: '30', label: '1 d / 30 s' }, { value: '10', label: '1 d / 10 s' }, { value: '4', label: '1 d / 4 s' }]
  const cur = speeds.find((s) => Number(s.value) === Math.round(clock.day_seconds))?.value ?? '30'
  const fail = (e: Error) => notify(e.message, 'err')
  return (
    <div role="region" aria-label="Simulation sandbox controls"
      className="flex h-9 shrink-0 items-center gap-3 border-b border-sun/40 bg-sun/[0.06] px-4 text-[11px] text-fg-2">
      <FlaskConical size={13} className="shrink-0 text-sun" />
      <span className="font-semibold text-sun">Simulation sandbox</span>
      <span className="hidden truncate text-fg-3 lg:inline">a copy of the live farm · nothing here changes the real-time twin</span>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <button className="btn btn-ghost px-2 py-0.5" aria-label={clock.paused ? 'Resume simulation' : 'Pause simulation'}
          onClick={() => worldApi.clock({ paused: !clock.paused }).catch(fail)}>
          {clock.paused ? <><Play size={12} />Resume</> : <><Pause size={12} />Pause</>}</button>
        <span className="flex items-center gap-1.5 text-fg-3"><FastForward size={12} />Speed</span>
        <Segmented size="xs" value={cur} options={speeds} onChange={(v) => worldApi.clock({ day_seconds: Number(v) }).catch(fail)} />
        <span className="mx-1 h-4 w-px bg-line" />
        <button className="btn btn-ghost px-2 py-0.5" title="Discard this run and start again from the current real-time state"
          onClick={() => { if (window.confirm('Discard this simulation run and start a new one from the current real-time state?')) worldApi.resetSimulation().then(() => notify('Sandbox restarted from the real-time twin', 'ok')).catch(fail) }}>
          <RotateCcw size={12} />Restart from live</button>
        <button className="btn px-2 py-0.5" onClick={() => setMode('realtime', notify)}><span className="h-1.5 w-1.5 rounded-full bg-leaf" />Back to real-time</button>
      </div>
    </div>
  )
}
