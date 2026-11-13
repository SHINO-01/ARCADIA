/**
 * Sprint 1 + 2 workflow: draw boundary -> H3 tessellation -> K-Means infrastructure -> activate twin.
 */
import { useEffect, useState } from 'react'
import { Activity, Hexagon, Layers, MapPinned, Play, Trash2, Wand2 } from 'lucide-react'
import { api } from '../lib/api'
import { fmt } from '../lib/format'
import type { Farm } from '../lib/types'
import { useUI } from '../store/ui'
import { Chip, Panel, Stat } from '../components/ui'

export function PlanningPanel({ drawn }: { drawn: { feature: GeoJSON.Feature<GeoJSON.Polygon>; areaM2: number } | null }) {
  const { farms, farm, setFarms, setFarm, setMode, notify, layer, setLayer } = useUI()
  const [name, setName] = useState('North Field')
  const [targetCells, setTargetCells] = useState(320)
  const [kDocks, setKDocks] = useState<number | ''>('')
  const [busy, setBusy] = useState<string | null>(null)
  const [stats, setStats] = useState<{ states: Record<string, number>; weeds: number; surveyed: number; avg_moisture: number } | null>(null)

  const refreshFarms = () => api.farms().then((fs) => { setFarms(fs); const active = fs.find((f) => f.status === 'active'); if (!farm && active) { setFarm(active); setMode('ops') } }).catch(() => {})
  useEffect(() => { refreshFarms() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!farm) return
    const load = () => api.farmStats(farm.id).then(setStats).catch(() => {})
    load(); const id = setInterval(load, 8000); return () => clearInterval(id)
  }, [farm?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (label: string, fn: () => Promise<Farm | void>) => {
    setBusy(label)
    try { const f = await fn(); if (f) setFarm(f); await refreshFarms() } catch (e) { notify((e as Error).message, 'err') } finally { setBusy(null) }
  }
  const create = () => drawn && run('tessellating', async () => {
    const f = await api.createFarm({ name, boundary: drawn.feature, target_cells: targetCells })
    notify(`${f.cell_count} H3 cells at resolution ${f.h3_resolution}`, 'ok'); setMode('plan'); return f
  })
  const plan = () => farm && run('clustering', async () => { const f = await api.plan(farm.id, { k_docks: kDocks === '' ? null : kDocks }); notify(`K-Means placed ${f.plan?.k_docks} docks · mean travel ${f.plan?.mean_travel_m} m`, 'ok'); return f })
  const activate = () => farm && run('activating', async () => { const f = await api.activate(farm.id); notify('Digital twin live — fleet provisioned', 'ok'); setMode('ops'); return f })
  const remove = () => farm && run('deleting', async () => { await api.deleteFarm(farm.id); setFarm(null); setMode('draw'); setStats(null) })

  const total = stats ? Object.values(stats.states).reduce((a, b) => a + b, 0) : 0

  return (
    <Panel title={<span className="flex items-center gap-1.5"><MapPinned size={12} />Spatial engine</span>}
      right={farms.length > 0 && (
        <select className="rounded border border-line bg-bg px-1.5 py-0.5 text-[11px]" value={farm?.id ?? ''} onChange={(e) => { const f = farms.find((x) => x.id === e.target.value) ?? null; setFarm(f); setMode(f ? (f.status === 'mapped' ? 'plan' : 'ops') : 'draw') }}>
          <option value="">— new farm —</option>{farms.map((f) => <option key={f.id} value={f.id}>{f.name} {f.status === 'active' ? '●' : ''}</option>)}
        </select>)} bodyClass="p-3 space-y-3 text-xs">

      {/* Step 1: boundary */}
      {!farm && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-fg-2"><Hexagon size={12} className="text-leaf" /><b>1 · Select boundary</b><span className="text-fg-3">use the polygon tool on the map</span></div>
          {drawn ? <Chip tone="ok">{fmt.ha(drawn.areaM2)} drawn</Chip> : <Chip>no polygon yet</Chip>}
          <input className="w-full rounded border border-line bg-bg px-2 py-1" value={name} onChange={(e) => setName(e.target.value)} placeholder="Farm name" />
          <label className="block text-[10px] text-fg-3">target hex count <span className="mono text-fg">{targetCells}</span> <span className="text-fg-3">(resolution auto-selected)</span>
            <input type="range" min={60} max={1200} step={20} value={targetCells} onChange={(e) => setTargetCells(Number(e.target.value))} className="w-full accent-leaf" /></label>
          <button className="btn btn-primary w-full justify-center" disabled={!drawn || !!busy} onClick={create}><Wand2 size={12} />{busy === 'tessellating' ? 'Tessellating…' : 'Generate H3 hex-grid'}</button>
        </div>
      )}

      {farm && (
        <>
          <div className="flex items-center justify-between">
            <div><div className="text-sm font-semibold">{farm.name}</div><div className="text-[10px] text-fg-3">{fmt.ha(farm.area_m2)} · {farm.centroid_lat.toFixed(5)}, {farm.centroid_lng.toFixed(5)}</div></div>
            <Chip tone={farm.status === 'active' ? 'ok' : farm.status === 'planned' ? 'info' : 'neutral'}>{farm.status}</Chip>
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            <Stat label="H3 res" value={farm.h3_resolution} />
            <Stat label="cells" value={farm.cell_count} />
            <Stat label="cell edge" value={`${(Math.sqrt(farm.area_m2 / farm.cell_count / 2.598)).toFixed(1)} m`} />
          </div>

          {/* Step 2: infrastructure */}
          <div className="space-y-2 border-t border-line pt-2">
            <div className="flex items-center gap-2 text-fg-2"><Layers size={12} className="text-sun" /><b>2 · Infrastructure (K-Means++)</b></div>
            <div className="flex items-center gap-2">
              <label className="text-[10px] text-fg-3">docks <input type="number" min={1} max={12} className="ml-1 w-14 rounded border border-line bg-bg px-1.5 py-0.5" placeholder="auto" value={kDocks} onChange={(e) => setKDocks(e.target.value === '' ? '' : Number(e.target.value))} /></label>
              <button className="btn ml-auto" disabled={!!busy} onClick={plan}><Activity size={12} />{busy === 'clustering' ? 'Clustering…' : farm.plan ? 'Re-plan' : 'Plan'}</button>
            </div>
            {farm.plan && (
              <div className="grid grid-cols-3 gap-1.5">
                <Stat label="docks" value={farm.plan.k_docks} sub={`${farm.plan.docks.filter((d) => d.kind === 'solar_dock').length} solar · ${farm.plan.docks.filter((d) => d.kind === 'wind_dock').length} wind`} />
                <Stat label="mean travel" value={fmt.m(farm.plan.mean_travel_m)} sub={`p95 ${fmt.m(farm.plan.p95_travel_m)}`} tone="ok" />
                <Stat label="iterations" value={farm.plan.iterations} sub={farm.plan.converged ? 'converged' : 'max iter'} />
              </div>
            )}
          </div>

          {/* Step 3: activate */}
          <div className="space-y-2 border-t border-line pt-2">
            <div className="flex items-center gap-2 text-fg-2"><Play size={12} className="text-sky" /><b>3 · Digital twin</b></div>
            <div className="flex gap-1.5">
              <button className="btn btn-primary flex-1 justify-center" disabled={!farm.plan || !!busy || farm.status === 'active'} onClick={activate}>{busy === 'activating' ? 'Spawning…' : farm.status === 'active' ? 'Twin is live' : 'Activate & spawn fleet'}</button>
              <button className="btn btn-danger" disabled={!!busy} onClick={remove} title="Delete farm"><Trash2 size={12} /></button>
            </div>
            {farm.status === 'active' && stats && (
              <>
                <div className="flex items-center gap-1 text-[10px] text-fg-3">layer
                  {(['state', 'moisture', 'health', 'crop'] as const).map((l) => <button key={l} className={`rounded px-1.5 py-0.5 ${layer === l ? 'bg-leaf-2/20 text-leaf' : 'hover:bg-line'}`} onClick={() => setLayer(l)}>{l}</button>)}
                </div>
                <div className="grid grid-cols-4 gap-1.5">
                  <Stat label="surveyed" value={`${total ? Math.round((stats.surveyed / total) * 100) : 0}%`} />
                  <Stat label="growing" value={(stats.states.seeded ?? 0) + (stats.states.growing ?? 0)} />
                  <Stat label="ripe" value={stats.states.ripe ?? 0} tone={stats.states.ripe ? 'warn' : undefined} />
                  <Stat label="weeds" value={stats.weeds} tone={stats.weeds ? 'bad' : 'ok'} />
                </div>
              </>
            )}
          </div>
        </>
      )}
    </Panel>
  )
}
