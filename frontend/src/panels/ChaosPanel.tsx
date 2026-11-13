import { useEffect, useState } from 'react'
import { Bug, Skull } from 'lucide-react'
import { api } from '../lib/api'
import type { ChaosConfig } from '../lib/types'
import { useLive } from '../store/live'
import { useUI } from '../store/ui'
import { Panel } from '../components/ui'

const KINDS = ['motor_jam', 'battery_drop', 'network_latency', 'sensor_glitch']

export function ChaosPanel() {
  const [cfg, setCfg] = useState<ChaosConfig | null>(null)
  const fleet = useLive((s) => s.fleet)
  const notify = useUI((s) => s.notify)
  const [target, setTarget] = useState('')
  const [kind, setKind] = useState('')
  useEffect(() => { api.chaosConfig().then(setCfg).catch(() => {}) }, [])
  const save = async (patch: Partial<ChaosConfig>) => { if (!cfg) return; const next = { ...cfg, ...patch }; setCfg(next); await api.setChaosConfig(next) }
  return (
    <Panel title={<span className="flex items-center gap-1.5"><Skull size={12} />Chaos engine</span>}
      right={cfg && <label className="flex items-center gap-1.5 text-[10px] text-fg-2"><input type="checkbox" checked={cfg.enabled} onChange={(e) => save({ enabled: e.target.checked })} className="accent-leaf" />{cfg.enabled ? 'armed' : 'disarmed'}</label>} bodyClass="p-2 space-y-2 text-xs">
      {cfg && (
        <div className="grid grid-cols-2 gap-2">
          <label className="text-[10px] text-fg-3">interval <span className="mono text-fg">{cfg.interval_s}s</span>
            <input type="range" min={10} max={600} step={10} value={cfg.interval_s} onChange={(e) => setCfg({ ...cfg, interval_s: Number(e.target.value) })} onMouseUp={() => save({})} onTouchEnd={() => save({})} className="w-full accent-rust" /></label>
          <label className="text-[10px] text-fg-3">probability <span className="mono text-fg">{Math.round(cfg.probability * 100)}%</span>
            <input type="range" min={0} max={1} step={0.05} value={cfg.probability} onChange={(e) => setCfg({ ...cfg, probability: Number(e.target.value) })} onMouseUp={() => save({})} onTouchEnd={() => save({})} className="w-full accent-rust" /></label>
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {KINDS.map((k) => <label key={k} className="flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-[10px] text-fg-2"><input type="checkbox" className="accent-rust" checked={cfg?.kinds.includes(k) ?? true} onChange={(e) => cfg && save({ kinds: e.target.checked ? [...cfg.kinds, k] : cfg.kinds.filter((x) => x !== k) })} />{k}</label>)}
      </div>
      <div className="flex items-center gap-1.5 border-t border-line pt-2">
        <select value={target} onChange={(e) => setTarget(e.target.value)} className="rounded border border-line bg-bg px-1.5 py-1 text-[11px]"><option value="">random robot</option>{Object.values(fleet).filter((r) => r.active).map((r) => <option key={r.id} value={r.id}>{r.id}</option>)}</select>
        <select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded border border-line bg-bg px-1.5 py-1 text-[11px]"><option value="">random fault</option>{KINDS.map((k) => <option key={k} value={k}>{k}</option>)}</select>
        <button className="btn btn-danger ml-auto" onClick={() => api.inject({ robot_id: target || undefined, kind: kind || undefined }).then((r) => notify(`Injected ${r.fault} into ${r.robot_id}`, 'info')).catch((e) => notify(e.message, 'err'))}><Bug size={12} />inject</button>
      </div>
    </Panel>
  )
}
