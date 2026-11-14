/** Mission-control shell: left navigation rail + thin status bar. */
import { Activity, AlertTriangle, BarChart3, Box, Map as MapIcon, Radar, Radio, Satellite, Gauge, Database } from 'lucide-react'
import { clsx } from 'clsx'
import { useLive } from '../store/live'
import { useUI, type View } from '../store/ui'
import { Dot } from './ui'

const VIEWS: { id: View; label: string; icon: typeof MapIcon; key: string }[] = [
  { id: 'plan', label: 'Plan', icon: MapIcon, key: '1' },
  { id: 'ops', label: 'Operations', icon: Radar, key: '2' },
  { id: 'twin', label: '3D Twin', icon: Box, key: '3' },
  { id: 'alerts', label: 'Alerts', icon: AlertTriangle, key: '4' },
  { id: 'analytics', label: 'Analytics', icon: BarChart3, key: '5' },
]

export function NavRail() {
  const { view, setView, farm } = useUI()
  const incidents = useLive((s) => s.incidents.filter((i) => i.state !== 'resolved').length)
  const disabled = (v: View) => v !== 'plan' && farm?.status !== 'active'
  return (
    <nav className="flex w-[72px] shrink-0 flex-col items-center border-r border-line bg-panel py-3">
      <div className="mb-4 flex flex-col items-center gap-1">
        <svg width="28" height="28" viewBox="0 0 64 64"><polygon points="32,4 56,18 56,46 32,60 8,46 8,18" fill="none" stroke="#7CE38B" strokeWidth="4" /><polygon points="32,18 44,25 44,39 32,46 20,39 20,25" fill="#7CE38B" /></svg>
        <span className="text-[9px] font-bold tracking-[0.22em]">ARCADIA</span>
      </div>
      {VIEWS.map(({ id, label, icon: Icon, key }) => (
        <button key={id} onClick={() => setView(id)} disabled={disabled(id)} title={`${label} (${key})`}
          className={clsx('relative mb-1 flex w-16 flex-col items-center gap-1 rounded-lg px-1 py-2 text-[10px] transition-colors disabled:opacity-30',
            view === id ? 'bg-leaf-2/15 text-leaf' : 'text-fg-3 hover:bg-line hover:text-fg')}>
          <Icon size={18} />{label}
          {id === 'alerts' && incidents > 0 && <span className="absolute right-2 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-alert px-1 text-[9px] font-bold text-white">{incidents}</span>}
        </button>
      ))}
      <div className="mt-auto flex flex-col items-center gap-2 text-fg-3"><span className="kbd">1-5</span></div>
    </nav>
  )
}

export function StatusBar({ fps }: { fps: number }) {
  const connected = useLive((s) => s.connected)
  const bridge = useLive((s) => s.bridge)
  const rate = useLive((s) => s.telemetryRate)
  const fleet = useLive((s) => s.fleet)
  const { farm, view } = useUI()
  const active = Object.values(fleet).filter((r) => r.active).length
  const bridgeTone = bridge?.ros_connected ? '#7ce38b' : bridge?.mode === 'headless' ? '#f5c451' : '#ff4d5e'
  const title = { plan: 'Spatial planning', ops: 'Field operations', twin: 'Physics twin', alerts: 'Maintenance & chaos', analytics: 'Energy & yield analytics' }[view]
  return (
    <header className="flex h-11 items-center gap-4 border-b border-line bg-panel px-4 text-[11px] text-fg-2">
      <div className="flex items-center gap-2"><span className="text-sm font-semibold text-fg">{title}</span>
        {farm && <span className="text-fg-3">· {farm.name} <span className={clsx('chip ml-1', farm.status === 'active' ? 'bg-leaf-2/20 text-leaf' : 'bg-line text-fg-2')}>{farm.status}</span></span>}</div>
      <div className="ml-auto flex items-center gap-4">
        <span className="flex items-center gap-1.5" title="Dashboard WebSocket"><Dot color={connected ? '#7ce38b' : '#ff4d5e'} pulse={connected} />link</span>
        <span className="flex items-center gap-1.5" title={`Bridge: ${bridge?.mode ?? '—'}`}><Radio size={12} style={{ color: bridgeTone }} /><span className="mono">{bridge?.ros_connected ? 'ROS 2 · Webots' : bridge?.mode === 'headless' ? 'headless twin' : bridge?.mode ?? '—'}</span></span>
        <span className="mono flex items-center gap-1.5" title="telemetry frames/s"><Satellite size={12} className="text-sky" />{rate} Hz</span>
        <span className="mono flex items-center gap-1.5" title="units active / cap"><Activity size={12} className="text-leaf" />{active}/2</span>
        {(view === 'ops' || view === 'plan') && <span className="mono flex items-center gap-1.5" title="canvas fps"><Gauge size={12} className={fps >= 55 ? 'text-leaf' : fps >= 30 ? 'text-sun' : 'text-alert'} />{fps}</span>}
        <span className="mono flex items-center gap-1.5" title="telemetry rows ingested this session"><Database size={12} className="text-fg-3" />{bridge?.telemetry ?? 0}</span>
        <Clock />
      </div>
    </header>
  )
}

import { useEffect, useState } from 'react'
function Clock() {
  const [t, setT] = useState(new Date())
  useEffect(() => { const id = setInterval(() => setT(new Date()), 1000); return () => clearInterval(id) }, [])
  return <span className="mono text-fg-3">{t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
}
