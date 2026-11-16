/** View compositions for the mission-control shell. The map itself is mounted once in App. */
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { fmt } from '../lib/format'
import { CROP_COLORS } from '../lib/types'
import { useLive } from '../store/live'
import { useUI } from '../store/ui'
import { Chip, Panel, Spark, Stat } from '../components/ui'
import { WebotsView } from '../components/WebotsView'
import { PlanningPanel } from '../panels/PlanningPanel'
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
    <div className="grid h-full gap-2 p-2" style={{ gridTemplateColumns: '360px 1fr' }}>
      <PlanningPanel drawn={drawn} onStartDraw={onStartDraw} onClearDraw={onClearDraw} />
      <MapSlot />
    </div>
  )
}

/* ------------------------------------------------------------------ Operations */
export function OpsView() {
  const feed = useLive((s) => s.feed)
  return (
    <div className="grid h-full gap-2 p-2" style={{ gridTemplateColumns: '300px 1fr' }}>
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
    <div className="grid h-full gap-2 p-2" style={{ gridTemplateRows: '1fr auto' }}>
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
    <div className="grid h-full gap-2 p-2" style={{ gridTemplateColumns: '1fr 1fr', gridTemplateRows: '1fr 1fr' }}>
      <div className="row-span-2 min-h-0"><IncidentsPanel /></div>
      <ChaosPanel />
      <FeedPanel />
    </div>
  )
}

/* ------------------------------------------------------------------ Analytics */
export function AnalyticsView() {
  const [yields, setYields] = useState<{ by_crop: { crop: string; kg: number; harvests: number }[]; total_kg: number } | null>(null)
  const [fleetStats, setFleetStats] = useState<{ robot_id: string; samples: number; avg_speed: number; min_battery: number; max_motor_temp: number }[]>([])
  const [history, setHistory] = useState<Record<string, number[]>>({})
  const farm = useUI((s) => s.farm)
  const [stats, setStats] = useState<{ states: Record<string, number>; weeds: number; surveyed: number; avg_moisture: number } | null>(null)
  useEffect(() => {
    const load = async () => {
      api.yields().then(setYields).catch(() => {}); api.status().then(() => {}).catch(() => {})
      fetch('/api/analytics/fleet').then((r) => r.json()).then(setFleetStats).catch(() => {})
      if (farm) api.farmStats(farm.id).then(setStats).catch(() => {})
      for (const rid of Object.keys(useLive.getState().fleet)) {
        api.telemetry(rid, 15).then((rows) => setHistory((h) => ({ ...h, [rid]: rows.map((r) => Number(r.battery_pct)) }))).catch(() => {})
      }
    }
    load(); const id = setInterval(load, 15000); return () => clearInterval(id)
  }, [farm?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const total = stats ? Object.values(stats.states).reduce((a, b) => a + b, 0) : 0
  return (
    <div className="grid h-full gap-2 p-2" style={{ gridTemplateColumns: '1fr 1fr', gridTemplateRows: 'auto 1fr 1fr' }}>
      <div className="col-span-2 grid grid-cols-6 gap-2">
        <Stat label="surveyed" value={`${total ? Math.round(((stats?.surveyed ?? 0) / total) * 100) : 0}%`} />
        <Stat label="seeded / growing" value={(stats?.states.seeded ?? 0) + (stats?.states.growing ?? 0)} />
        <Stat label="ripe" value={stats?.states.ripe ?? 0} tone={stats?.states.ripe ? 'warn' : undefined} />
        <Stat label="weed pressure" value={stats?.weeds ?? 0} tone={stats?.weeds ? 'bad' : 'ok'} />
        <Stat label="avg moisture" value={`${Math.round((stats?.avg_moisture ?? 0) * 100)}%`} />
        <Stat label="yield to date" value={`${yields?.total_kg ?? 0} kg`} tone="ok" />
      </div>
      <EnergyPanel />
      <Panel title="Yields by crop" bodyClass="p-3 space-y-2">
        {!yields?.by_crop.length && <p className="text-xs text-fg-3">No harvests yet. Beds ripen on an accelerated clock once seeded.</p>}
        {yields?.by_crop.map((y) => <div key={y.crop} className="text-xs"><div className="flex justify-between"><span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: CROP_COLORS[y.crop] }} />{y.crop}</span><span className="mono">{y.kg} kg · {y.harvests} harvests</span></div>
          <div className="mt-1 h-1.5 rounded-full bg-line"><div className="h-full rounded-full" style={{ width: `${Math.min(100, (y.kg / Math.max(1, yields.total_kg)) * 100)}%`, background: CROP_COLORS[y.crop] }} /></div></div>)}
      </Panel>
      <Panel title="Fleet telemetry · last hour" bodyClass="p-3">
        <table className="w-full text-left text-xs"><thead className="text-[10px] uppercase text-fg-3"><tr><th>unit</th><th>samples</th><th>avg speed</th><th>min battery</th><th>max motor °C</th><th>battery 15 min</th></tr></thead>
          <tbody>{fleetStats.map((r) => <tr key={r.robot_id} className="border-t border-line"><td className="py-1 font-medium">{r.robot_id}</td><td className="mono">{r.samples}</td><td className="mono">{r.avg_speed} m/s</td><td className="mono">{r.min_battery}%</td><td className="mono">{r.max_motor_temp}</td><td><Spark data={history[r.robot_id] ?? []} w={120} h={20} max={100} /></td></tr>)}</tbody></table>
      </Panel>
      <MissionsPanel />
    </div>
  )
}

export function ChipRow({ items }: { items: string[] }) { return <div className="flex flex-wrap gap-1">{items.map((i) => <Chip key={i}>{i}</Chip>)}</div> }
