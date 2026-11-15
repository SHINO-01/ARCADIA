/** Plan view sidebar: boundary → H3 grid → infrastructure (docks, storage, repair, roads, beds) → activate. */
import { useEffect, useState } from 'react'
import { Activity, Hexagon, Layers, MapPinned, Pencil, Play, RefreshCw, Trash2, Wand2 } from 'lucide-react'
import { api } from '../lib/api'
import { fmt } from '../lib/format'
import { CROP_COLORS, type Farm, type PlanParams } from '../lib/types'
import { useUI } from '../store/ui'
import { Chip, Panel, Stat } from '../components/ui'

export function PlanningPanel({ drawn, onStartDraw, onClearDraw }: { drawn: { feature: GeoJSON.Feature<GeoJSON.Polygon>; areaM2: number } | null; onStartDraw: () => void; onClearDraw: () => void }) {
  const { farms, farm, setFarms, setFarm, setMode, setView, notify } = useUI()
  const [name, setName] = useState('North Field')
  const [targetCells, setTargetCells] = useState(320)
  const [params, setParams] = useState<PlanParams>({ road_spacing: 6, bed_width: 3, orientation: 'ew', shuffle_crops: false, k_docks: null })
  const [busy, setBusy] = useState<string | null>(null)

  // On first load, jump to the active farm; later refreshes never override what the user selected.
  const refreshFarms = (autoSelect = false) => api.farms().then((fs) => {
    setFarms(fs)
    if (autoSelect && !useUI.getState().farm) { const active = fs.find((f) => f.status === 'active'); if (active) { setFarm(active); setMode('plan') } }
  }).catch(() => {})
  useEffect(() => { refreshFarms(true) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (farm?.plan) setParams((p) => ({ ...p, road_spacing: farm.plan!.road_spacing, bed_width: farm.plan!.bed_width, orientation: farm.plan!.orientation })) }, [farm?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (label: string, fn: () => Promise<Farm | void>) => {
    setBusy(label)
    try { const f = await fn(); if (f) setFarm(f); await refreshFarms(false) } catch (e) { notify((e as Error).message, 'err') } finally { setBusy(null) }
  }
  const create = () => drawn && run('tessellating', async () => { const f = await api.createFarm({ name, boundary: drawn.feature, target_cells: targetCells }); notify(`${f.cell_count} H3 cells at resolution ${f.h3_resolution}`, 'ok'); setMode('plan'); onClearDraw(); return f })
  const plan = (extra: Partial<PlanParams> = {}) => farm && run('clustering', async () => { const p = { ...params, ...extra }; setParams(p); const f = await api.plan(farm.id, p); notify(`${f.plan?.k_docks} docks · ${f.plan?.road_cells ?? 0} road cells · ${(f.plan?.beds ?? []).length} beds`, 'ok'); return f })
  const activate = () => farm && run('activating', async () => { const f = await api.activate(farm.id); notify('Digital twin live — fleet provisioned', 'ok'); setView('ops'); return f })
  const remove = () => farm && run('deleting', async () => { await api.deleteFarm(farm.id); setFarm(null); setMode('draw') })
  const reset = () => run('resetting', async () => { if (!confirm('Start a fresh session? This deletes every farm, plan, mission and incident.')) return; await api.resetAll(); setFarm(null); setFarms([]); setMode('draw'); onClearDraw(); notify('Workspace reset', 'ok') })

  return (
    <Panel title={<span className="flex items-center gap-1.5"><MapPinned size={12} />Spatial engine</span>}
      right={<div className="flex items-center gap-1">
        {farms.length > 0 && <select className="rounded border border-line bg-bg px-1.5 py-0.5 text-[11px]" value={farm?.id ?? ''} onChange={(e) => { const f = farms.find((x) => x.id === e.target.value) ?? null; setFarm(f); setMode(f ? 'plan' : 'draw') }}>
          <option value="">— new farm —</option>{farms.map((f) => <option key={f.id} value={f.id}>{f.name}{f.status === 'active' ? ' ●' : ''}</option>)}</select>}
        <button className="btn" title="Fresh session (delete everything)" onClick={reset}><RefreshCw size={12} /></button>
      </div>} bodyClass="p-3 space-y-3 text-xs">

      {!farm && (
        <section className="space-y-2">
          <div className="flex items-center gap-2 text-fg-2"><Hexagon size={12} className="text-leaf" /><b>1 · Select boundary</b></div>
          <div className="flex gap-1.5">
            <button className="btn btn-primary flex-1 justify-center" onClick={onStartDraw}><Pencil size={12} />Draw boundary</button>
            <button className="btn" disabled={!drawn} onClick={onClearDraw}><Trash2 size={12} /></button>
          </div>
          <p className="text-[10px] text-fg-3">Click vertices on the satellite map; double-click (or click the first vertex) to close the polygon.</p>
          {drawn ? <Chip tone="ok">{fmt.ha(drawn.areaM2)} selected</Chip> : <Chip>no boundary yet</Chip>}
          <input className="w-full rounded border border-line bg-bg px-2 py-1" value={name} onChange={(e) => setName(e.target.value)} placeholder="Farm name" />
          <label className="block text-[10px] text-fg-3">target hex count <span className="mono text-fg">{targetCells}</span> · resolution auto
            <input type="range" min={60} max={1200} step={20} value={targetCells} onChange={(e) => setTargetCells(Number(e.target.value))} className="w-full accent-leaf" /></label>
          <button className="btn btn-primary w-full justify-center" disabled={!drawn || !!busy} onClick={create}><Wand2 size={12} />{busy === 'tessellating' ? 'Tessellating…' : 'Generate H3 hex-grid'}</button>
        </section>
      )}

      {farm && (<>
        <div className="flex items-center justify-between">
          <div><div className="text-sm font-semibold">{farm.name}</div><div className="text-[10px] text-fg-3">{fmt.ha(farm.area_m2)} · {farm.centroid_lat.toFixed(5)}, {farm.centroid_lng.toFixed(5)}</div></div>
          <Chip tone={farm.status === 'active' ? 'ok' : farm.status === 'planned' ? 'info' : 'neutral'}>{farm.status}</Chip>
        </div>
        <div className="grid grid-cols-3 gap-1.5"><Stat label="H3 res" value={farm.h3_resolution} /><Stat label="cells" value={farm.cell_count} /><Stat label="cell edge" value={`${Math.sqrt(farm.area_m2 / farm.cell_count / 2.598).toFixed(1)} m`} /></div>

        <section className="space-y-2 border-t border-line pt-2">
          <div className="flex items-center gap-2 text-fg-2"><Layers size={12} className="text-sun" /><b>2 · Infrastructure layout</b></div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] text-fg-3">
            <label>road spacing <span className="mono text-fg">{params.road_spacing} rows</span><input type="range" min={3} max={14} value={params.road_spacing} onChange={(e) => setParams({ ...params, road_spacing: Number(e.target.value) })} className="w-full accent-sun" /></label>
            <label>bed width <span className="mono text-fg">{params.bed_width} strips</span><input type="range" min={1} max={6} value={params.bed_width} onChange={(e) => setParams({ ...params, bed_width: Number(e.target.value) })} className="w-full accent-sun" /></label>
            <label className="flex items-center gap-1">beds <select className="rounded border border-line bg-bg px-1 py-0.5 text-[10px]" value={params.orientation} onChange={(e) => setParams({ ...params, orientation: e.target.value as 'ew' | 'ns' })}><option value="ew">east–west</option><option value="ns">north–south</option></select></label>
            <label className="flex items-center gap-1">docks <input type="number" min={1} max={12} placeholder="auto" className="w-12 rounded border border-line bg-bg px-1 py-0.5" value={params.k_docks ?? ''} onChange={(e) => setParams({ ...params, k_docks: e.target.value === '' ? null : Number(e.target.value) })} /></label>
          </div>
          <div className="flex gap-1.5">
            <button className="btn btn-primary flex-1 justify-center" disabled={!!busy} onClick={() => plan()}><Activity size={12} />{busy === 'clustering' ? 'Planning…' : farm.plan ? 'Re-plan layout' : 'Plan infrastructure'}</button>
            <button className="btn" disabled={!!busy || !farm.plan} title="Rotate crops across beds" onClick={() => plan({ shuffle_crops: true, seed: Math.floor(Math.random() * 1e6) })}>↻ crops</button>
          </div>
          {farm.plan && (<>
            <div className="grid grid-cols-3 gap-1.5">
              <Stat label="docks" value={farm.plan.k_docks} sub={`${farm.plan.docks.filter((d) => d.kind === 'solar_dock').length} solar · ${farm.plan.docks.filter((d) => d.kind === 'wind_dock').length} wind`} />
              <Stat label="mean travel" value={fmt.m(farm.plan.mean_travel_m)} sub={`p95 ${fmt.m(farm.plan.p95_travel_m)}`} tone="ok" />
              <Stat label="roads" value={farm.plan.road_cells ?? 0} sub={farm.plan.road_cells == null ? 're-plan for roads & beds' : `${farm.plan.crop_cells} crop cells`} />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] uppercase tracking-wide text-fg-3">Crop beds ({(farm.plan.beds ?? []).length})</div>
              <div className="scroll max-h-28 space-y-0.5 overflow-auto pr-1">
                {(farm.plan.beds ?? []).map((b) => <div key={b.id} className="flex items-center gap-2 text-[11px]"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: CROP_COLORS[b.crop] }} /><span className="font-medium">{b.id.replace('bed-', 'Bed ')}</span><span className="text-fg-3">{b.crop}</span><span className="mono ml-auto text-fg-3">{b.cells.length} cells</span></div>)}
              </div>
              <div className="flex flex-wrap gap-1 pt-1">{[...farm.plan.docks, ...farm.plan.storage, ...(farm.plan.repair ?? [])].map((s) => <Chip key={s.slug} tone={s.kind === 'repair_station' ? 'warn' : s.kind === 'storage' ? 'info' : 'ok'}>{s.slug}</Chip>)}</div>
            </div>
          </>)}
        </section>

        <section className="space-y-2 border-t border-line pt-2">
          <div className="flex items-center gap-2 text-fg-2"><Play size={12} className="text-sky" /><b>3 · Digital twin</b></div>
          <div className="flex gap-1.5">
            <button className="btn btn-primary flex-1 justify-center" disabled={!farm.plan || !!busy || farm.status === 'active'} onClick={activate}>{busy === 'activating' ? 'Spawning…' : farm.status === 'active' ? 'Twin is live' : 'Activate & spawn fleet'}</button>
            <button className="btn btn-danger" disabled={!!busy} onClick={remove} title="Delete this farm"><Trash2 size={12} /></button>
          </div>
          {farm.status === 'active' && <p className="text-[10px] text-fg-3">Re-planning pushes the new layout to the running simulation.</p>}
        </section>
      </>)}
    </Panel>
  )
}
