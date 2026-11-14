import { useEffect, useState } from 'react'
import { Bug, Skull, Zap } from 'lucide-react'
import { api } from '../lib/api'
import type { ChaosConfig, ChaosPreset } from '../lib/types'
import { useLive } from '../store/live'
import { useUI } from '../store/ui'
import { Panel } from '../components/ui'

const KINDS = ['motor_jam', 'battery_drop', 'network_latency', 'sensor_glitch', 'stuck_terrain']

export function ChaosPanel() {
  const [cfg, setCfg] = useState<ChaosConfig | null>(null)
  const [presets, setPresets] = useState<ChaosPreset[]>([])
  const fleet = useLive((s) => s.fleet)
  const notify = useUI((s) => s.notify)
  const [target, setTarget] = useState('')
  const [kind, setKind] = useState('')
  useEffect(() => { api.chaosConfig().then(setCfg).catch(() => {}); api.chaosPresets().then(setPresets).catch(() => {}) }, [])
  const save = async (patch: Partial<ChaosConfig>) => { if (!cfg) return; const next = { ...cfg, ...patch }; setCfg(next); await api.setChaosConfig(next) }
  return (
    <Panel title={<span className="flex items-center gap-1.5"><Skull size={12} />Chaos engine</span>}
      right={cfg && <label className="flex items-center gap-1.5 text-[10px] text-fg-2"><input type="checkbox" checked={cfg.enabled} onChange={(e) => save({ enabled: e.target.checked })} className="accent-leaf" />{cfg.enabled ? 'armed' : 'disarmed'}</label>} bodyClass="p-3 space-y-3 text-xs">
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-wide text-fg-3">Scenario presets</div>
        <div className="grid grid-cols-2 gap-1.5">
          {presets.map((p) => <button key={p.id} className="btn justify-start" onClick={() => api.runPreset(p.id).then((r) => notify(`${r.fault} → ${r.robot_id}`, 'info')).catch((e) => notify(e.message, 'err'))}><Zap size={12} className="text-rust" />{p.label}</button>)}
        </div>
      </div>
      {cfg && (
        <div className="grid grid-cols-2 gap-2 border-t border-line pt-2">
          <label className="text-[10px] text-fg-3">random interval <span className="mono text-fg">{cfg.interval_s}s</span>
            <input type="range" min={30} max={900} step={30} value={cfg.interval_s} onChange={(e) => setCfg({ ...cfg, interval_s: Number(e.target.value) })} onMouseUp={() => save({})} onTouchEnd={() => save({})} className="w-full accent-rust" /></label>
          <label className="text-[10px] text-fg-3">probability <span className="mono text-fg">{Math.round(cfg.probability * 100)}%</span>
            <input type="range" min={0} max={1} step={0.05} value={cfg.probability} onChange={(e) => setCfg({ ...cfg, probability: Number(e.target.value) })} onMouseUp={() => save({})} onTouchEnd={() => save({})} className="w-full accent-rust" /></label>
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {KINDS.map((k) => <label key={k} className="flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-[10px] text-fg-2"><input type="checkbox" className="accent-rust" checked={cfg?.kinds.includes(k as ChaosConfig['kinds'][number]) ?? true} onChange={(e) => cfg && save({ kinds: (e.target.checked ? [...cfg.kinds, k] : cfg.kinds.filter((x) => x !== k)) as ChaosConfig['kinds'] })} />{k}</label>)}
      </div>
      <div className="flex items-center gap-1.5 border-t border-line pt-2">
        <select value={target} onChange={(e) => setTarget(e.target.value)} className="rounded border border-line bg-bg px-1.5 py-1 text-[11px]"><option value="">random unit</option>{Object.values(fleet).filter((r) => r.active).map((r) => <option key={r.id} value={r.id}>{r.id}</option>)}</select>
        <select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded border border-line bg-bg px-1.5 py-1 text-[11px]"><option value="">random fault</option>{KINDS.map((k) => <option key={k} value={k}>{k}</option>)}</select>
        <button className="btn btn-danger ml-auto" onClick={() => api.inject({ robot_id: target || undefined, kind: kind || undefined }).then((r) => notify(`Injected ${r.fault} into ${r.robot_id}`, 'info')).catch((e) => notify(e.message, 'err'))}><Bug size={12} />inject</button>
      </div>
    </Panel>
  )
}
