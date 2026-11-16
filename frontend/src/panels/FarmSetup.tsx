/** Farm setup wizard: boundary -> site analysis -> adaptive layout -> crop -> go live. */
import { useEffect, useState, type ReactNode } from 'react'
import { clsx } from 'clsx'
import { Check, ChevronDown, CloudSun, Eye, Layers, Loader2, MapPinned, Pencil, Play, RefreshCw, ScanSearch, Sprout, Trash2, Wand2 } from 'lucide-react'
import * as turf from '@turf/turf'
import { api } from '../lib/api'
import { fmt } from '../lib/format'
import { CROP_COLORS, type Farm, type PlanParams } from '../lib/types'
import { MONTHS, fmtDate, worldApi, type Analysis, type CropOption } from '../lib/world'
import { useUI } from '../store/ui'
import { useWorld } from '../store/world'
import { Chip, Segmented, Toggle } from '../components/ui'

type Drawn = { feature: GeoJSON.Feature<GeoJSON.Polygon>; areaM2: number } | null
const DEFAULT_PARAMS: PlanParams = { bed_width: 6, lane_width: 2.4, headland_width: 10, section_length: 40, alley_spacing: 0, orientation: 'auto', shuffle_crops: false, k_docks: null }

export function FarmSetup({ drawn, onStartDraw, onClearDraw }: { drawn: Drawn; onStartDraw: () => void; onClearDraw: () => void }) {
  const { farms, farm, setFarms, setFarm, setMode, setView, notify, overlays, setOverlays } = useUI()
  const [name, setName] = useState('North Field')
  const [busy, setBusy] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const live = useWorld((s) => (farm ? s.analysis[farm.id] : undefined))

  const refreshFarms = (autoSelect = false) => api.farms().then((fs) => {
    setFarms(fs)
    if (autoSelect && !useUI.getState().farm) {
      const active = fs.find((f) => f.status === 'active')
      if (active) { setFarm(active); setMode('plan') }
      if (!fs.length) useUI.getState().setOnboarding(true)
    }
  }).catch(() => {})
  useEffect(() => { refreshFarms(true) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!farm) { setAnalysis(null); return }
    let stop = false
    const poll = () => worldApi.analysis(farm.id).then((a) => { if (!stop) setAnalysis(a) }).catch(() => {})
    poll()
    const id = setInterval(() => { if (!analysis?.analysed || (analysis.status && !analysis.status.done)) poll() }, 2500)
    return () => { stop = true; clearInterval(id) }
  }, [farm?.id, live?.done]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (label: string, fn: () => Promise<Farm | void>) => {
    setBusy(label)
    try { const f = await fn(); if (f) setFarm(f); await refreshFarms(false) } catch (e) { notify((e as Error).message, 'err') } finally { setBusy(null) }
  }
  const status = live ?? analysis?.status ?? null
  const analysing = !!status && !status.done
  const step = !farm ? 1 : !analysis?.analysed || analysing ? 2 : !farm.plan ? 3 : !farm.crop_plan ? 4 : farm.status === 'active' ? 6 : 5
  const isOpen = (n: number) => open === n || (open === null && step === n)

  const create = () => drawn && run('create', async () => {
    const f = await api.createFarm({ name, boundary: drawn.feature })
    onClearDraw()
    setMode('plan')
    await worldApi.analyze(f.id)
    notify(`${f.name} saved: analysing the site`, 'ok')
    return f
  })
  const remove = () => farm && run('delete', async () => { if (!confirm(`Delete ${farm.name}?`)) return; await api.deleteFarm(farm.id); setFarm(null); setMode('draw') })
  const reset = () => run('reset', async () => {
    if (!confirm('Start a fresh session? This deletes every farm, plan, mission and incident.')) return
    await api.resetAll(); setFarm(null); setFarms([]); setMode('draw'); onClearDraw(); useUI.getState().setOnboarding(true); notify('Workspace reset', 'ok')
  })

  return (
    <aside className="panel flex min-h-0 flex-col">
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <MapPinned size={15} className="text-leaf" />
        <span className="text-[13px] font-semibold">Farm setup</span>
        <div className="ml-auto flex items-center gap-1.5">
          {farms.length > 0 && <select aria-label="Farm" className="field w-auto py-1" value={farm?.id ?? ''} onChange={(e) => { const f = farms.find((x) => x.id === e.target.value) ?? null; setFarm(f); setMode(f ? 'plan' : 'draw'); setOpen(null) }}>
            <option value="">+ New farm</option>{farms.map((f) => <option key={f.id} value={f.id}>{f.name}{f.status === 'active' ? ' (live)' : ''}</option>)}</select>}
          <button className="btn btn-ghost px-2" title="Fresh session (delete everything)" aria-label="Fresh session" onClick={reset}><RefreshCw size={13} /></button>
        </div>
      </header>
      <div className="scroll min-h-0 flex-1 space-y-1 overflow-auto p-3">
        <Step n={1} title="Field boundary" icon={<Pencil size={14} />} step={step} open={isOpen(1)} onToggle={() => setOpen(isOpen(1) ? -1 : 1)}
          summary={farm ? `${farm.name} · ${fmt.ha(farm.area_m2)}` : undefined}>
          {!farm ? (<div className="space-y-2.5">
            <p className="text-xs text-fg-2">Outline the field on the satellite map. Any shape works: the layout adapts to it, to the roads already there and to the soil.</p>
            <div className="flex gap-1.5">
              <button className="btn btn-solid flex-1" onClick={onStartDraw}><Pencil size={13} />Draw field boundary</button>
              <button className="btn" disabled={!drawn} onClick={onClearDraw} aria-label="Clear boundary"><Trash2 size={13} /></button>
            </div>
            <p className="text-[11px] text-fg-3">Click to place corners · click the first corner, double-click or <span className="kbd">Enter</span> to close · <span className="kbd">⌫</span> undo</p>
            {drawn && <>
              <Chip tone="ok">{fmt.ha(drawn.areaM2)} · {fmt.m(turf.length(turf.lineString(drawn.feature.geometry.coordinates[0]), { units: 'meters' }))} perimeter</Chip>
              <label className="block"><span className="label">Farm name</span><input className="field mt-1" value={name} onChange={(e) => setName(e.target.value)} /></label>
              <button className="btn btn-primary w-full" disabled={!!busy} onClick={create}>{busy === 'create' ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}Create farm and analyse the site</button>
            </>}
          </div>) : (
            <div className="flex items-center justify-between text-xs"><span className="text-fg-2">{fmt.ha(farm.area_m2)} · {farm.centroid_lat.toFixed(4)}, {farm.centroid_lng.toFixed(4)}</span>
              <button className="btn btn-danger px-2 py-1" onClick={remove} aria-label="Delete farm"><Trash2 size={12} /></button></div>
          )}
        </Step>

        <Step n={2} title="Site analysis" icon={<ScanSearch size={14} />} step={step} open={isOpen(2)} onToggle={() => setOpen(isOpen(2) ? -1 : 2)}
          summary={analysis?.analysed ? `${analysis.imagery?.roads.length ?? 0} roads · ${analysis.soil?.texture ?? '—'} · ${analysis.climate?.annual_rain_mm ?? '—'} mm/yr` : undefined}>
          {farm && <SiteAnalysis farm={farm} analysis={analysis} status={status} onRerun={() => worldApi.analyze(farm.id).then(() => notify('Re-analysing the site', 'info'))}
            overlays={overlays} setOverlays={setOverlays} />}
        </Step>

        <Step n={3} title="Adaptive layout" icon={<Layers size={14} />} step={step} open={isOpen(3)} onToggle={() => setOpen(isOpen(3) ? -1 : 3)}
          summary={farm?.plan?.layout ? `${farm.plan.layout.sections} sections · ${farm.plan.layout.cropped_pct}% cropped` : undefined}>
          {farm && <LayoutStep farm={farm} busy={busy} run={run} />}
        </Step>

        <Step n={4} title="Crop" icon={<Sprout size={14} />} step={step} open={isOpen(4)} onToggle={() => setOpen(isOpen(4) ? -1 : 4)}
          summary={farm?.crop_plan ? String((farm.crop_plan as { crop?: string }).crop ?? '') : undefined}>
          {farm?.plan && <CropStep farm={farm} run={run} />}
        </Step>

        <Step n={5} title="Go live" icon={<Play size={14} />} step={step} open={isOpen(5)} onToggle={() => setOpen(isOpen(5) ? -1 : 5)}
          summary={farm?.status === 'active' ? 'twin is live' : undefined}>
          {farm?.plan && (farm.status === 'active' ? (
            <div className="space-y-2">
              <p className="text-xs text-fg-2">The farm is live: weather, soil water and crop growth are simulated day by day, and the robots work the field.</p>
              <div className="flex gap-1.5"><button className="btn btn-primary flex-1" onClick={() => setView('world')}>Open the world model</button><button className="btn flex-1" onClick={() => setView('ops')}>Operations</button></div>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-fg-2">Spawns the farm in Webots and starts the world model at the crop's next sowing window.</p>
              <button className="btn btn-solid w-full" disabled={!!busy} onClick={() => run('activate', async () => { const f = await api.activate(farm.id); notify('Digital twin live: fleet provisioned', 'ok'); setView('world'); return f })}>
                {busy === 'activate' ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}Activate farm</button>
            </div>
          ))}
        </Step>
      </div>
    </aside>
  )
}

function Step({ n, title, icon, step, open, onToggle, summary, children }: { n: number; title: string; icon: ReactNode; step: number; open: boolean; onToggle: () => void; summary?: string; children: ReactNode }) {
  const done = step > n
  const locked = step < n
  return (
    <section className={clsx('rounded-xl border transition-colors duration-200', open && !locked ? 'border-line-2 bg-panel-2/70' : 'border-transparent')}>
      <button className="flex w-full cursor-pointer items-center gap-2.5 px-3 py-2.5 text-left disabled:cursor-default" disabled={locked} onClick={onToggle} aria-expanded={open}>
        <span className={clsx('flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold',
          done ? 'border-leaf-2 bg-leaf-2/25 text-leaf' : step === n ? 'border-leaf text-leaf' : 'border-line-2 text-fg-3')}>{done ? <Check size={12} /> : n}</span>
        <span className={clsx('text-[13px] font-medium', locked ? 'text-fg-3' : 'text-fg')}>{title}</span>
        <span className="text-fg-3">{icon}</span>
        {summary && !open && <span className="ml-auto truncate pl-2 text-[11px] text-fg-3">{summary}</span>}
        {!locked && <ChevronDown size={14} className={clsx('shrink-0 text-fg-3 transition-transform duration-200', summary && !open ? '' : 'ml-auto', open && 'rotate-180')} />}
      </button>
      {open && !locked && <div className="anim-fade px-3 pb-3">{children}</div>}
    </section>
  )
}

function SiteAnalysis({ farm, analysis, status, onRerun, overlays, setOverlays }: {
  farm: Farm; analysis: Analysis | null; status: Analysis['status']; onRerun: () => void
  overlays: { detections: boolean; imagery: boolean; soil: string | null }; setOverlays: (o: Partial<{ detections: boolean; imagery: boolean; soil: string | null }>) => void
}) {
  const steps = [{ k: 'imagery', label: 'Satellite imagery: roads, buildings, water, trees' }, { k: 'climate', label: 'Climate: 10 years of ERA5 weather' }, { k: 'soil', label: 'Soil: ISRIC SoilGrids' }]
  const order = ['imagery', 'climate', 'soil', 'done']
  const at = status ? order.indexOf(status.step) : -1
  if (status && !status.done) return (
    <div className="space-y-2">
      <div className="h-1.5 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-leaf transition-[width] duration-700" style={{ width: `${status.pct}%` }} /></div>
      <ul className="space-y-1.5 text-xs">{steps.map((s, i) => <li key={s.k} className="flex items-center gap-2">
        {i < at ? <Check size={12} className="text-leaf" /> : i === at ? <Loader2 size={12} className="animate-spin text-sun" /> : <span className="h-3 w-3 rounded-full border border-line-2" />}
        <span className={i <= at ? 'text-fg' : 'text-fg-3'}>{s.label}</span></li>)}</ul>
      <p className="text-[11px] text-fg-3">{status.detail}</p>
    </div>
  )
  if (!analysis?.analysed) return <button className="btn btn-primary w-full" onClick={onRerun}><ScanSearch size={13} />Analyse the site</button>
  const im = analysis.imagery
  const kinds = (k: string) => im?.obstacles.filter((o) => o.kind === k && o.inside).length ?? 0
  const roadKm = (im?.roads.reduce((a, r) => a + r.length_m, 0) ?? 0) / 1000
  return (
    <div className="space-y-3 text-xs">
      <div className="grid grid-cols-2 gap-2">
        <Fact label="Existing roads" value={`${im?.roads.length ?? 0}`} sub={`${roadKm.toFixed(1)} km, ${im?.roads.filter((r) => r.inside).length ?? 0} cross the field`} />
        <Fact label="Obstacles inside" value={`${kinds('building') + kinds('water') + kinds('trees')}`} sub={`${kinds('building')} buildings · ${kinds('water')} water · ${kinds('trees')} trees`} />
        <Fact label="Climate" value={`${analysis.climate?.annual_rain_mm ?? '—'} mm`} sub={`${analysis.climate?.annual_tmean ?? '—'} °C mean · ${analysis.climate?.source ?? ''}`} />
        <Fact label="Soil" value={analysis.soil?.texture ?? '—'} sub={`sand ${analysis.soil?.sand?.toFixed(0)}% · clay ${analysis.soil?.clay?.toFixed(0)}% · OM ${analysis.soil?.om}%`} />
      </div>
      {analysis.climate && <RainBars probs={analysis.climate.monthly_rain_prob} />}
      {im?.error && <p className="text-[11px] text-sun">Imagery: {im.error}</p>}
      <div className="space-y-1.5 border-t border-line pt-2.5">
        <label className="flex items-center gap-2"><Toggle checked={overlays.detections} onChange={(v) => setOverlays({ detections: v })} label="Show detections" /><span>Detected roads and obstacles</span></label>
        <label className="flex items-center gap-2"><Toggle checked={overlays.imagery} onChange={(v) => setOverlays({ imagery: v })} label="Show classification" /><span>Image classification overlay</span><Eye size={12} className="text-fg-3" /></label>
      </div>
      <p className="text-[10px] text-fg-3">{analysis.soil?.source} · {im?.source} at {im?.gsd_m} m/px · farm {farm.name}</p>
      <button className="btn btn-ghost w-full" onClick={onRerun}><RefreshCw size={12} />Re-run analysis</button>
    </div>
  )
}

function RainBars({ probs }: { probs: number[] }) {
  const mx = Math.max(...probs, 0.01)
  return (
    <div>
      <div className="label mb-1 flex items-center gap-1.5"><CloudSun size={11} />Chance of a wet day by month</div>
      <div className="flex h-10 items-end gap-0.5">{probs.map((p, i) => <div key={i} className="flex flex-1 flex-col items-center gap-0.5" title={`${MONTHS[i]}: ${Math.round(p * 100)}%`}>
        <div className="w-full rounded-sm bg-sky/70" style={{ height: `${(p / mx) * 28 + 2}px` }} /><span className="text-[8px] text-fg-3">{MONTHS[i][0]}</span></div>)}</div>
    </div>
  )
}

function Fact({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="rounded-lg border border-line bg-bg/50 px-2.5 py-2"><div className="label">{label}</div><div className="mt-0.5 text-sm font-semibold capitalize">{value}</div>{sub && <div className="mt-0.5 text-[10px] leading-snug text-fg-3">{sub}</div>}</div>
}

function LayoutStep({ farm, busy, run }: { farm: Farm; busy: string | null; run: (label: string, fn: () => Promise<Farm | void>) => void }) {
  const notify = useUI((s) => s.notify)
  const [p, setP] = useState<PlanParams & { use_roads?: boolean }>({ ...DEFAULT_PARAMS, use_roads: true })
  useEffect(() => { const lp = farm.plan?.layout?.params; if (lp) setP((x) => ({ ...x, ...lp, k_docks: null, k_storage: null })) }, [farm.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const L = farm.plan?.layout as (NonNullable<Farm['plan']>['layout'] & { zones?: { id: number; angle_deg: number; area_m2: number; sections: number; bed_runs?: number }[]; roads_used?: number; obstacles_excluded?: number; baseline?: { score: number; optimised_score: number; bed_runs: number; angle_deg: number } }) | undefined
  const gain = L?.baseline ? (100 * (L.baseline.optimised_score - L.baseline.score)) / Math.max(1, Math.abs(L.baseline.score)) : null
  const runs = L?.zones?.reduce((a, z) => a + (z.bed_runs ?? 0), 0)
  const plan = () => run('plan', async () => {
    const f = await api.plan(farm.id, p)
    const l = f.plan?.layout
    notify(`${l?.sections ?? 0} sections in ${(l as { zones?: unknown[] })?.zones?.length ?? 1} zone(s) · ${l?.cropped_pct}% cropped`, 'ok')
    return f
  })
  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center gap-2"><span className="label">Bed direction</span>
        <Segmented value={p.orientation} onChange={(v) => setP({ ...p, orientation: v })} size="xs" options={[{ value: 'auto', label: 'Adaptive' }, { value: 'ew', label: 'E–W' }, { value: 'ns', label: 'N–S' }]} /></div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-[11px] text-fg-3">
        <Slider label="Bed width" unit="m" v={p.bed_width} min={1.5} max={18} step={0.5} on={(v) => setP({ ...p, bed_width: v })} />
        <Slider label="Tramline" unit="m" v={p.lane_width} min={1.6} max={6} step={0.2} on={(v) => setP({ ...p, lane_width: v })} />
        <Slider label="Headland" unit="m" v={p.headland_width} min={5} max={30} step={1} on={(v) => setP({ ...p, headland_width: v })} />
        <Slider label="Section" unit="m" v={p.section_length} min={10} max={120} step={5} on={(v) => setP({ ...p, section_length: v })} />
      </div>
      <label className="flex items-center gap-2"><Toggle checked={p.use_roads !== false} onChange={(v) => setP({ ...p, use_roads: v })} label="Reuse existing roads" />Reuse existing roads as farm tracks</label>
      <button className="btn btn-primary w-full" disabled={!!busy} onClick={plan}>{busy === 'plan' ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}{farm.plan ? 'Re-optimise layout' : 'Optimise layout'}</button>
      {busy === 'plan' && <p className="text-[11px] text-fg-3">Searching bed directions per zone, testing reflex-vertex splits, scoring soil-weighted bed area against headland turns…</p>}
      {L && (<div className="space-y-2.5 border-t border-line pt-2.5">
        <div className="grid grid-cols-3 gap-2">
          <Fact label="Cropped" value={`${L.cropped_pct}%`} sub={`${fmt.ha(L.crop_area_m2)} of beds`} />
          <Fact label="Sections" value={`${L.sections}`} sub={`${L.beds.length} beds`} />
          <Fact label="Tracks" value={`${(L.track_length_m / 1000).toFixed(1)} km`} sub={`${L.tramlines} tramlines`} />
        </div>
        {gain != null && <div className="rounded-lg border border-leaf-2/40 bg-leaf-2/10 px-3 py-2">
          <div className="font-medium text-leaf">{gain >= 0 ? '+' : ''}{gain.toFixed(1)}% productive score vs. one-direction layout</div>
          <div className="mt-0.5 text-[11px] text-fg-2">{runs} bed runs instead of {L.baseline?.bed_runs} (fewer headland turns) · {L.roads_used ?? 0} existing road segments reused · {L.obstacles_excluded ?? 0} obstacles avoided</div>
        </div>}
        {!!L.zones?.length && <div><div className="label mb-1">Management zones</div>
          <ul className="space-y-1">{L.zones.slice(0, 6).map((z) => <li key={z.id} className="flex items-center gap-2 text-[11px]"><span className="inline-block h-3 w-3 rounded-sm border border-line-2" style={{ transform: `rotate(${-z.angle_deg}deg)`, background: 'linear-gradient(90deg, var(--color-leaf-2) 50%, transparent 50%)', backgroundSize: '4px 100%' }} />
            <span>Zone {z.id + 1}</span><span className="text-fg-3">{z.angle_deg.toFixed(0)}° · {fmt.ha(z.area_m2)}</span><span className="num ml-auto text-fg-3">{z.sections} sections</span></li>)}</ul></div>}
      </div>)}
    </div>
  )
}

function Slider({ label, unit, v, min, max, step, on }: { label: string; unit: string; v: number; min: number; max: number; step: number; on: (v: number) => void }) {
  return <label className="block">{label} <span className="num text-fg">{v} {unit}</span><input type="range" min={min} max={max} step={step} value={v} onChange={(e) => on(Number(e.target.value))} className="w-full" /></label>
}

function CropStep({ farm, run }: { farm: Farm; run: (label: string, fn: () => Promise<Farm | void>) => void }) {
  const notify = useUI((s) => s.notify)
  const [opts, setOpts] = useState<CropOption[] | null>(null)
  const [loading, setLoading] = useState(false)
  const current = (farm.crop_plan as { crop?: string; rotation_crop?: string | null } | null) ?? null
  const [pick, setPick] = useState<string | null>(current?.crop ?? null)
  const [rotation, setRotation] = useState<string>(current?.rotation_crop ?? '')
  const load = (refresh = false) => { setLoading(true); worldApi.crops(farm.id, refresh).then((o) => { setOpts(o); if (!pick) setPick(o.find((x) => x.recommended)?.crop ?? o[0]?.crop ?? null) }).catch((e) => notify(e.message, 'err')).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [farm.id, farm.plan]) // eslint-disable-line react-hooks/exhaustive-deps
  const maxMargin = Math.max(...(opts ?? []).map((o) => o.margin_usd_ha.p90), 1)
  return (
    <div className="space-y-2.5 text-xs">
      <p className="text-fg-2">One crop per field, as real farms do. Each option is simulated for a full season on this farm's soils and climate (Monte Carlo over weather).</p>
      {loading && !opts && <div className="space-y-1.5">{[0, 1, 2, 3].map((i) => <div key={i} className="shimmer h-14 rounded-lg border border-line" />)}<p className="text-[11px] text-fg-3">Simulating seasons…</p></div>}
      {opts && <ul className="space-y-1.5" role="radiogroup">{opts.map((o) => (
        <li key={o.crop}><button role="radio" aria-checked={pick === o.crop} onClick={() => setPick(o.crop)}
          className={clsx('w-full cursor-pointer rounded-lg border px-3 py-2 text-left transition-colors duration-200', pick === o.crop ? 'border-leaf bg-leaf-2/10' : 'border-line hover:border-line-2')}>
          <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: CROP_COLORS[o.crop] ?? '#888' }} /><span className="font-medium">{o.name}</span>
            {o.recommended && <Chip tone="ok">recommended</Chip>}<span className="num ml-auto text-fg-2">{o.yield_t_ha.p50.toFixed(1)} t/ha</span></div>
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-sun/80" style={{ width: `${Math.max(0, (o.margin_usd_ha.p50 / maxMargin) * 100)}%` }} /></div>
          <div className="num mt-1 flex justify-between text-[10px] text-fg-3"><span>margin ${Math.round(o.margin_usd_ha.p50)}/ha (P10 ${Math.round(o.margin_usd_ha.p10)})</span><span>sow {fmtDate(o.sow_date)} · {o.season_days} d · risk {Math.round(o.risk_cv * 100)}%</span></div>
        </button></li>))}</ul>}
      {opts && <label className="block"><span className="label">Rotation block (optional)</span>
        <select className="field mt-1" value={rotation} onChange={(e) => setRotation(e.target.value)}><option value="">None: whole field one crop</option>{opts.filter((o) => o.crop !== pick).map((o) => <option key={o.crop} value={o.crop}>{o.name} on half the beds</option>)}</select></label>}
      <div className="flex gap-1.5">
        <button className="btn btn-primary flex-1" disabled={!pick} onClick={() => run('crop', async () => { await worldApi.setCrop(farm.id, { crop: pick!, rotation_crop: rotation || null }); notify(`Crop set: ${pick}${rotation ? ` + ${rotation}` : ''}`, 'ok'); return api.farm(farm.id) })}><Sprout size={13} />Use {opts?.find((o) => o.crop === pick)?.name ?? 'crop'}</button>
        <button className="btn" title="Re-simulate" aria-label="Re-simulate crop options" onClick={() => load(true)}><RefreshCw size={13} /></button>
      </div>
    </div>
  )
}
