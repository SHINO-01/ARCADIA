import { CROP_COLORS, INFRA_COLOR, TRACK_COLOR } from '../lib/types'
import { useUI } from '../store/ui'

export function Legend({ compact = false }: { compact?: boolean }) {
  const { layer, setLayer, farm } = useUI()
  const beds = farm?.plan?.layout?.beds ?? []
  const crops = Array.from(new Set(beds.map((b) => b.crop)))
  return (
    <div className="pointer-events-auto rounded-lg border border-line bg-panel/90 p-2.5 text-[10px] backdrop-blur">
      <div className="mb-1.5 flex items-center gap-1">
        {(['plan', 'state', 'moisture', 'health'] as const).map((l) => <button key={l} className={`rounded px-1.5 py-0.5 capitalize ${layer === l ? 'bg-leaf-2/20 text-leaf' : 'text-fg-3 hover:bg-line'}`} onClick={() => setLayer(l)}>{l === 'plan' ? 'crops' : l}</button>)}
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        {(layer === 'plan' || layer === 'crop') && crops.map((c) => <Item key={c} color={CROP_COLORS[c] ?? '#888'} label={`${c} bed`} />)}
        {layer === 'state' && [['fallow', '#6b5a3e'], ['seeded', '#8a7a45'], ['growing', '#3fb95a'], ['ripe', '#f5c451'], ['harvested', '#4b5f52']].map(([l, c]) => <Item key={l} color={c} label={l} />)}
        {layer === 'moisture' && [['dry', '#8a6a3d'], ['moist', '#5cc8ff'], ['saturated', '#1d5fd1']].map(([l, c]) => <Item key={l} color={c} label={l} />)}
        {layer === 'health' && [['stressed', '#ff4d5e'], ['fair', '#f5c451'], ['healthy', '#7ce38b']].map(([l, c]) => <Item key={l} color={c} label={l} />)}
        <Item color={TRACK_COLOR.lane} label="tramline (dirt)" />
        <Item color={TRACK_COLOR.headland} label="headland / alley" />
        <Item color={TRACK_COLOR.spur} label="yard spur" />
        <Item color={INFRA_COLOR.solar_dock} label="solar dock" ring />
        <Item color={INFRA_COLOR.wind_dock} label="wind dock" ring />
        <Item color={INFRA_COLOR.storage} label="robot storage" ring />
        <Item color={INFRA_COLOR.repair_station} label="repair station" ring />
        {!compact && <><Item dot="#ff4d5e" label="weeds detected" /><Item hatch="#5cc8ff" label="waterlogged spot" /></>}
      </div>
      {!compact && <p className="mt-1.5 max-w-[16rem] text-fg-3">Ground vehicles only drive on tracks and work beds from the tramlines. Only a harvester drives down the bed it cuts.</p>}
    </div>
  )
}
function Item({ color, label, ring, hatch, dot }: { color?: string; label: string; ring?: boolean; hatch?: string; dot?: string }) {
  return <span className="flex items-center gap-1.5 text-fg-2">
    {hatch ? <span className="inline-block h-3 w-3 rounded-sm" style={{ background: `repeating-linear-gradient(45deg, ${hatch} 0 2px, transparent 2px 5px)`, border: '1px solid #22332a' }} />
      : dot ? <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: dot, border: '1px solid #0b1410' }} />
      : ring ? <span className="inline-block h-3 w-3 rounded-full" style={{ border: `2px solid ${color}`, background: `${color}40` }} />
        : <span className="inline-block h-3 w-3 rounded-sm" style={{ background: color }} />}{label}</span>
}
