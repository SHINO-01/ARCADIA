import { useState } from 'react'
import { ChevronDown, Layers } from 'lucide-react'
import { clsx } from 'clsx'
import { CROP_COLORS, INFRA_COLOR, TRACK_COLOR } from '../lib/types'
import { STAGE_COLORS, STAGE_LABEL } from '../lib/world'
import { useUI, type MapLayer } from '../store/ui'

const LAYERS: { id: MapLayer; label: string }[] = [
  { id: 'state', label: 'Stage' }, { id: 'ndvi', label: 'NDVI' }, { id: 'moisture', label: 'Water' }, { id: 'health', label: 'Health' }, { id: 'yield', label: 'Yield' }, { id: 'plan', label: 'Crop' },
]
const RAMPS: Partial<Record<MapLayer, { stops: string[]; lo: string; hi: string; note: string }>> = {
  ndvi: { stops: ['#8a6a3d', '#c9b46a', '#7fc45a', '#1f7a3c'], lo: '0.1', hi: '0.9', note: 'UAV multispectral, latest pass' },
  moisture: { stops: ['#8a6a3d', '#c9a96a', '#5fade8', '#1d5fd1'], lo: 'dry', hi: 'saturated', note: 'root-zone water, UAV estimate' },
  health: { stops: ['#f06464', '#e2b44a', '#5fd68e'], lo: 'stressed', hi: 'healthy', note: 'water, weeds and disease combined' },
  yield: { stops: ['#4b3a26', '#c9a640', '#e2b44a', '#fff1b8'], lo: '0', hi: '14 t/ha', note: 'UAV estimate from flowering on' },
}
const SOIL = [{ id: 'clay', label: 'Clay %' }, { id: 'om', label: 'Organic matter' }, { id: 'taw', label: 'Available water' }, { id: 'ec', label: 'Salinity' }, { id: 'twi', label: 'Wetness (TWI)' }, { id: 'elevation', label: 'Elevation' }]

export function Legend({ compact = false }: { compact?: boolean }) {
  const { layer, setLayer, farm, overlays, setOverlays } = useUI()
  const [open, setOpen] = useState(false)
  const crops = Array.from(new Set((farm?.plan?.layout?.beds ?? []).map((b) => b.crop)))
  const ramp = RAMPS[layer]
  return (
    <div className="pointer-events-auto w-[260px] rounded-xl border border-line bg-panel/95 text-[11px] shadow-xl backdrop-blur">
      <button className="flex w-full cursor-pointer items-center gap-2 px-3 py-2" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Layers size={13} className="text-fg-3" /><span className="font-medium">Map layers</span>
        <ChevronDown size={13} className={clsx('ml-auto text-fg-3 transition-transform duration-200', open && 'rotate-180')} />
      </button>
      {open && <div className="space-y-2.5 border-t border-line px-3 pb-3 pt-2.5">
        <div className="flex flex-wrap gap-1">
          {LAYERS.map((l) => <button key={l.id} onClick={() => setLayer(l.id)} className={clsx('cursor-pointer rounded-md px-2 py-0.5 transition-colors duration-200', layer === l.id ? 'bg-leaf-2/20 text-leaf' : 'text-fg-3 hover:bg-line hover:text-fg')}>{l.label}</button>)}
        </div>
        {layer === 'state' && <div className="grid grid-cols-2 gap-x-3 gap-y-1">{Object.keys(STAGE_LABEL).map((s) => <Item key={s} color={STAGE_COLORS[s]} label={STAGE_LABEL[s]} />)}</div>}
        {(layer === 'plan' || layer === 'crop') && <div className="grid grid-cols-2 gap-x-3 gap-y-1">{crops.map((c) => <Item key={c} color={CROP_COLORS[c] ?? '#888'} label={c} />)}</div>}
        {ramp && <div><div className="h-2 rounded-full" style={{ background: `linear-gradient(90deg, ${ramp.stops.join(',')})` }} />
          <div className="mt-1 flex justify-between text-[10px] text-fg-3"><span>{ramp.lo}</span><span>{ramp.hi}</span></div>
          <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-fg-3"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-[#3a4741]" />not yet scouted · {ramp.note}</div></div>}
        {!compact && <div className="grid grid-cols-2 gap-x-3 gap-y-1 border-t border-line pt-2">
          <Item color={TRACK_COLOR.lane} label="Tramline" />
          <Item color={TRACK_COLOR.headland} label="Headland / alley" />
          <Item color="#7d7f86" label="Existing road" />
          <Item color={INFRA_COLOR.solar_dock} label="Charging dock" ring />
          <Item color={INFRA_COLOR.storage} label="Storage" ring />
          <Item color={INFRA_COLOR.repair_station} label="Repair" ring />
          <Item dot="#f06464" label="Weeds" />
          <Item hatch="#5fade8" label="Wet spot" />
        </div>}
        {!compact && <div className="space-y-1.5 border-t border-line pt-2">
          <div className="label">Overlays</div>
          <label className="flex cursor-pointer items-center gap-2"><input type="checkbox" checked={overlays.detections} onChange={(e) => setOverlays({ detections: e.target.checked })} />Detected roads & obstacles</label>
          <label className="flex cursor-pointer items-center gap-2"><input type="checkbox" checked={overlays.imagery} onChange={(e) => setOverlays({ imagery: e.target.checked })} />Image classification</label>
          {farm?.status === 'active' && <select aria-label="Soil map" className="field py-1" value={overlays.soil ?? ''} onChange={(e) => setOverlays({ soil: e.target.value || null })}>
            <option value="">No soil map</option>{SOIL.map((s) => <option key={s.id} value={s.id}>Soil: {s.label}</option>)}</select>}
        </div>}
      </div>}
    </div>
  )
}
function Item({ color, label, ring, hatch, dot }: { color?: string; label: string; ring?: boolean; hatch?: string; dot?: string }) {
  return <span className="flex items-center gap-1.5 capitalize text-fg-2">
    {hatch ? <span className="inline-block h-3 w-3 rounded-sm" style={{ background: `repeating-linear-gradient(45deg, ${hatch} 0 2px, transparent 2px 5px)`, border: '1px solid #1e2b25' }} />
      : dot ? <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: dot, border: '1px solid #0a0f0d' }} />
      : ring ? <span className="inline-block h-3 w-3 rounded-full" style={{ border: `2px solid ${color}`, background: `${color}40` }} />
        : <span className="inline-block h-3 w-3 rounded-sm" style={{ background: color }} />}{label}</span>
}
