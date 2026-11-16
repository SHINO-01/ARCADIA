/** View compositions for the mission-control shell. The map itself is mounted once in App. */
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { fmt } from '../lib/format'
import { CROP_COLORS } from '../lib/types'
import { useLive } from '../store/live'
import { useUI } from '../store/ui'
import { Card, Chip, Empty, Kpi, Panel, Spark } from '../components/ui'
import { Legend, TimeChart } from '../components/charts'
import { fmtDate, worldApi } from '../lib/world'
import { useWorld } from '../store/world'
import { clsx } from 'clsx'
import { Activity, Droplets, ShieldCheck, Sprout, Wheat } from 'lucide-react'
import { WebotsView } from '../components/WebotsView'
import { FarmSetup } from '../panels/FarmSetup'
import { FleetPanel } from '../panels/FleetPanel'
import { MissionsPanel } from '../panels/MissionsPanel'
import { IncidentsPanel } from '../panels/IncidentsPanel'
import { EnergyPanel } from '../panels/EnergyPanel'
import { ChaosPanel } from '../panels/ChaosPanel'
import { FeedPanel } from '../panels/FeedPanel'

/* ------------------------------------------------------------------ Plan */
/** Placeholder the single map instance is positioned over (see App). */
export function MapSlot() {
  const setMapSlot = useUI((s) => s.setMapSlot)
  return <div ref={setMapSlot} className="min-h-0 rounded-lg border border-line bg-panel" />
}

export function PlanView({ drawn, onStartDraw, onClearDraw }: { drawn: { feature: GeoJSON.Feature<GeoJSON.Polygon>; areaM2: number } | null; onStartDraw: () => void; onClearDraw: () => void }) {
  return (
    <div className="grid h-full gap-3 p-3" style={{ gridTemplateColumns: '380px 1fr' }}>
      <FarmSetup drawn={drawn} onStartDraw={onStartDraw} onClearDraw={onClearDraw} />
      <MapSlot />
    </div>
  )
}

/* ------------------------------------------------------------------ Operations */
export function OpsView() {
  const feed = useLive((s) => s.feed)
  return (
    <div className="grid h-full gap-3 p-3" style={{ gridTemplateColumns: '300px 1fr' }}>
      <div className="flex min-h-0 flex-col gap-2">
        <FleetPanel />
        <div className="h-40 shrink-0"><Panel title="Live ticker" bodyClass="p-0">
          <ul className="divide-y divide-line/60">{feed.slice(0, 6).map((e) => <li key={e.id} className="flex items-center gap-2 px-3 py-1 text-[10px] text-fg-2"><span className="mono text-fg-3">{fmt.time(e.t / 1000)}</span><span className="font-medium">{e.kind}</span><span className="mono text-fg-3">{e.robot_id}</span></li>)}</ul>
        </Panel></div>
      </div>
      <MapSlot />
    </div>
  )
}

/* ------------------------------------------------------------------ 3D twin */
export function TwinView() {
  const fleet = useLive((s) => s.fleet)
  return (
    <div className="grid h-full gap-3 p-3" style={{ gridTemplateRows: '1fr auto' }}>
      <WebotsView />
      <div className="grid grid-cols-3 gap-2">
        {Object.values(fleet).sort((a, b) => a.id.localeCompare(b.id)).map((r) => (
          <div key={r.id} className="panel flex items-center gap-3 px-3 py-2 text-xs">
            <span className="font-semibold">{r.id}</span><span className="text-fg-3">{r.status}</span>
            <span className="mono ml-auto text-fg-2">{Math.round(r.battery_pct)}%</span>
            <span className="mono text-fg-3">{r.x?.toFixed(0)}, {r.y?.toFixed(0)} m</span>
          </div>))}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ Alerts */
export function AlertsView() {
  return (
    <div className="grid h-full gap-3 p-3" style={{ gridTemplateColumns: '1.1fr 1fr 1fr', gridTemplateRows: '1fr 1fr' }}>
      <div className="row-span-2 min-h-0"><AnomalyHistory /></div>
      <div className="min-h-0"><IncidentsPanel /></div>
      <div className="min-h-0"><ChaosPanel /></div>
      <div className="col-span-2 min-h-0"><FeedPanel /></div>
    </div>
  )
}

function AnomalyHistory() {
  const [rows, setRows] = useState<Awaited<ReturnType<typeof worldApi.anomalies>>>([])
  const live = useWorld((s) => s.anomalies.length)
  useEffect(() => { worldApi.anomalies().then(setRows).catch(() => {}) }, [live])
  useEffect(() => { const id = setInterval(() => worldApi.anomalies().then(setRows).catch(() => {}), 15000); return () => clearInterval(id) }, [])
  return (
    <Card title="Agronomic anomalies" icon={<Activity size={15} />} right={<Chip tone={rows.some((r) => r.active) ? 'warn' : 'ok'}>{rows.filter((r) => r.active).length} active</Chip>}>
      {!rows.length && <Empty icon={<ShieldCheck size={26} />} title="No anomalies so far">Weather against the farm's 10-year climatology, drought (SPI-30), soil probes, irrigation flow (CUSUM) and UAV NDVI outliers are screened every simulated day.</Empty>}
      <ul className="space-y-2">{rows.map((r) => (
        <li key={r.id} className={clsx('rounded-lg border-l-2 px-3 py-2', r.active ? (r.severity === 'critical' ? 'border-alert bg-alert/5' : 'border-sun bg-sun/5') : 'border-line-2 bg-panel-2/40 opacity-75')}>
          <div className="flex items-center gap-2 text-xs"><span className="font-semibold">{r.title}</span>{r.active ? <Chip tone={r.severity === 'critical' ? 'bad' : 'warn'}>{r.severity}</Chip> : <Chip>resolved</Chip>}
            <span className="num ml-auto text-[10px] text-fg-3">{fmtDate(r.started)}{r.ended ? ` – ${fmtDate(r.ended)}` : ''}</span></div>
          <div className="mt-0.5 text-[11px] text-fg-2">{r.detail}</div>
          {r.active && <div className="mt-1 text-[11px] text-leaf">→ {r.action}</div>}
          <div className="mt-1 text-[10px] text-fg-3">{r.method} · value {String(r.value)} vs threshold {String(r.threshold)}</div>
        </li>))}</ul>
    </Card>
  )
}

/* ------------------------------------------------------------------ Analytics */
export function AnalyticsView() {
  const [yields, setYields] = useState<{ by_crop: { crop: string; kg: number; harvests: number }[]; total_kg: number } | null>(null)
  const [fleetStats, setFleetStats] = useState<{ robot_id: string; samples: number; avg_speed: number; min_battery: number; max_motor_temp: number }[]>([])
  const [history, setHistory] = useState<Record<string, number[]>>({})
  const [daily, setDaily] = useState<Awaited<ReturnType<typeof worldApi.daily>>>([])
  const [wx, setWx] = useState<Awaited<ReturnType<typeof worldApi.weather>>['history']>([])
  useEffect(() => {
    const load = async () => {
      api.yields().then(setYields).catch(() => {})
      fetch('/api/analytics/fleet').then((r) => r.json()).then(setFleetStats).catch(() => {})
      worldApi.daily(365).then(setDaily).catch(() => {})
      worldApi.weather(365).then((r) => setWx(r.history)).catch(() => {})
      for (const rid of Object.keys(useLive.getState().fleet)) {
        api.telemetry(rid, 15).then((rows) => setHistory((h) => ({ ...h, [rid]: rows.map((r) => Number(r.battery_pct)) }))).catch(() => {})
      }
    }
    load(); const id = setInterval(load, 20000); return () => clearInterval(id)
  }, [])
  const byDate = Object.fromEntries(wx.map((d) => [d.date, d]))
  const water = daily.reduce<{ date: string; rain: number; et0: number; irr: number }[]>((acc, d) => {
    const w = byDate[d.date]
    const prev = acc[acc.length - 1] ?? { rain: 0, et0: 0, irr: 0 }
    acc.push({ date: d.date, rain: prev.rain + (w?.rain ?? 0), et0: prev.et0 + (w?.et0 ?? 0), irr: prev.irr + (d.irr_delivered_mm ?? 0) })
    return acc
  }, [])
  const totals = water[water.length - 1] ?? { rain: 0, et0: 0, irr: 0 }
  const last = daily[daily.length - 1]
  return (
    <div className="scroll h-full overflow-auto p-3">
      <div className="mx-auto grid max-w-[1700px] gap-3" style={{ gridTemplateColumns: 'repeat(12, minmax(0, 1fr))' }}>
        <div className="col-span-12 grid grid-cols-2 gap-3 md:grid-cols-5">
          <Kpi label="Harvested" value={yields ? (yields.total_kg / 1000).toFixed(1) : '—'} unit="t" tone="ok" caption={`${yields?.by_crop.reduce((a, y) => a + y.harvests, 0) ?? 0} section harvests`} />
          <Kpi label="Biomass now" value={last ? last.biomass_t_ha.toFixed(1) : '—'} unit="t/ha" />
          <Kpi label="Rain this season" value={Math.round(totals.rain)} unit="mm" tone="info" />
          <Kpi label="Irrigation applied" value={Math.round(totals.irr)} unit="mm" tone="info" caption="delivered at the root zone" />
          <Kpi label="Reference ET" value={Math.round(totals.et0)} unit="mm" caption="FAO-56 Penman-Monteith" />
        </div>
        <Card className="col-span-12 xl:col-span-6" title="Canopy and biomass" icon={<Sprout size={15} />} right={<Legend items={[{ label: 'LAI', color: 'var(--color-leaf)' }, { label: 'Biomass t/ha', color: 'var(--color-sun)' }]} />}>
          <TimeChart height={200} labels={daily.map((d) => d.date)} fmtX={(s) => fmtDate(s)} yMin={0} rightMin={0}
            series={[{ key: 'lai', label: 'LAI', values: daily.map((d) => d.lai), color: 'var(--color-leaf)', kind: 'area' },
              { key: 'bio', label: 'Biomass', unit: 't/ha', values: daily.map((d) => d.biomass_t_ha), color: 'var(--color-sun)', axis: 'right' }]} />
        </Card>
        <Card className="col-span-12 xl:col-span-6" title="Water balance (cumulative)" icon={<Droplets size={15} />} right={<Legend items={[{ label: 'Rain', color: '#4a7fd0' }, { label: 'Irrigation', color: 'var(--color-sky)' }, { label: 'ET₀', color: 'var(--color-sun)', dashed: true }]} />}>
          <TimeChart height={200} labels={water.map((d) => d.date)} fmtX={(s) => fmtDate(s)} yMin={0}
            series={[{ key: 'rain', label: 'Rain', unit: 'mm', values: water.map((d) => d.rain), color: '#4a7fd0', kind: 'area' },
              { key: 'irr', label: 'Irrigation', unit: 'mm', values: water.map((d) => d.irr), color: 'var(--color-sky)' },
              { key: 'et', label: 'ET₀', unit: 'mm', values: water.map((d) => d.et0), color: 'var(--color-sun)', dashed: true }]} />
        </Card>
        <Card className="col-span-12 lg:col-span-4" title="Yields by crop" icon={<Wheat size={15} />}>
          {!yields?.by_crop.length && <p className="text-xs text-fg-3">No harvest yet. Sections are harvested when the crop is ripe; an early cut yields nothing.</p>}
          {yields?.by_crop.map((y) => <div key={y.crop} className="mb-2 text-xs"><div className="flex justify-between"><span className="flex items-center gap-1.5 capitalize"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: CROP_COLORS[y.crop] }} />{y.crop}</span><span className="num">{(y.kg / 1000).toFixed(2)} t · {y.harvests} harvests</span></div>
            <div className="mt-1 h-1.5 rounded-full bg-line"><div className="h-full rounded-full" style={{ width: `${Math.min(100, (y.kg / Math.max(1, yields.total_kg)) * 100)}%`, background: CROP_COLORS[y.crop] }} /></div></div>)}
        </Card>
        <div className="col-span-12 lg:col-span-4 min-h-[260px]"><EnergyPanel /></div>
        <Card className="col-span-12 lg:col-span-4" title="Fleet · last hour" icon={<Activity size={15} />}>
          <table className="w-full text-left text-xs"><thead className="label"><tr><th className="pb-1 font-medium">unit</th><th className="font-medium">speed</th><th className="font-medium">min batt.</th><th className="font-medium">battery 15 min</th></tr></thead>
            <tbody>{fleetStats.map((r) => <tr key={r.robot_id} className="border-t border-line"><td className="py-1.5 font-medium">{r.robot_id}</td><td className="num">{r.avg_speed} m/s</td><td className="num">{r.min_battery}%</td><td><Spark data={history[r.robot_id] ?? []} w={110} h={20} max={100} /></td></tr>)}</tbody></table>
        </Card>
        <div className="col-span-12 min-h-[300px]"><MissionsPanel /></div>
      </div>
    </div>
  )
}

