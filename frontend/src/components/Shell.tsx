/** Mission-control shell: left navigation rail + status bar with the world clock (real-time twin or simulation sandbox). */
import { useEffect, useState } from 'react'
import { Activity, Database, Gauge, Radio, Satellite } from 'lucide-react'
import { clsx } from 'clsx'
import { useLive } from '../store/live'
import { VIEWS } from '../lib/nav'
import { useUI, type View } from '../store/ui'
import { useWorld } from '../store/world'
import { WorldClock } from '../views/WorldView'
import { Dot } from './ui'

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
      <rect x="4" y="4" width="56" height="56" rx="16" fill="#12201a" stroke="#2fa864" strokeWidth="2" />
      {[0, 1, 2, 3].map((i) => <rect key={i} x={14 + i * 9.5} y="16" width="5" height="32" rx="2.5" fill={i === 1 ? '#e2b44a' : '#5fd68e'} opacity={0.55 + i * 0.12} transform={`rotate(-18 32 32)`} />)}
    </svg>
  )
}

export function NavRail() {
  const { view, setView, farms, farm, setFarm } = useUI()
  const incidents = useLive((s) => s.incidents.filter((i) => i.state !== 'resolved').length)
  const anomalies = useWorld((s) => s.anomalies.length)
  const live = farms.find((f) => f.status === 'active') ?? (farm?.status === 'active' ? farm : null)
  const disabled = (v: View) => v !== 'plan' && !live
  // Operational views always show the live twin, even if a draft farm is open in the planner.
  const go = (v: View) => { if (v !== 'plan' && live && farm?.id !== live.id) setFarm(live); setView(v) }
  return (
    <nav aria-label="Views" className="flex w-[76px] shrink-0 flex-col items-center border-r border-line bg-panel py-3">
      <div className="mb-5 flex flex-col items-center gap-1.5">
        <Logo />
        <span className="text-[9px] font-semibold tracking-[0.24em] text-fg-2">ARCADIA</span>
      </div>
      {VIEWS.map(({ id, label, icon: Icon, key }) => {
        const badge = id === 'alerts' ? incidents + anomalies : 0
        return (
          <button key={id} onClick={() => go(id)} disabled={disabled(id)} title={`${label} (${key})`} aria-current={view === id ? 'page' : undefined}
            className={clsx('relative mb-1 flex w-[64px] cursor-pointer flex-col items-center gap-1 rounded-xl px-1 py-2.5 text-[10px] font-medium transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-30',
              view === id ? 'bg-leaf-2/15 text-leaf' : 'text-fg-3 hover:bg-line/70 hover:text-fg')}>
            {view === id && <span className="absolute -left-1.5 top-1/2 h-6 w-1 -translate-y-1/2 rounded-r bg-leaf" />}
            <Icon size={18} strokeWidth={1.8} />{label}
            {badge > 0 && <span className="num absolute right-1.5 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-alert px-1 text-[9px] font-bold text-white">{badge}</span>}
          </button>
        )
      })}
      <div className="mt-auto flex flex-col items-center gap-2 text-fg-3"><span className="kbd">1-6</span></div>
    </nav>
  )
}

const TITLES: Record<View, string> = { plan: 'Farm setup', world: 'World model', ops: 'Field operations', twin: 'Physics twin', alerts: 'Alerts & risk', analytics: 'Analytics' }

export function StatusBar({ fps }: { fps: number }) {
  const connected = useLive((s) => s.connected)
  const bridge = useLive((s) => s.bridge)
  const rate = useLive((s) => s.telemetryRate)
  const fleet = useLive((s) => s.fleet)
  const { farm, view } = useUI()
  const active = Object.values(fleet).filter((r) => r.active).length
  const sandbox = useWorld((s) => s.clock?.mode === 'simulation')
  const bridgeTone = bridge?.ros_connected ? 'var(--color-leaf)' : bridge?.mode === 'headless' ? 'var(--color-sun)' : 'var(--color-alert)'
  return (
    <header className={clsx('flex h-12 items-center gap-4 border-b bg-panel px-4 text-[11px] text-fg-2', sandbox ? 'border-sun/60 shadow-[inset_0_-1px_0_var(--color-sun)]' : 'border-line')}>
      <div className="flex min-w-0 items-center gap-2">
        <h1 className="whitespace-nowrap text-sm font-semibold text-fg">{TITLES[view]}</h1>
        {farm && <span className="hidden truncate text-fg-3 lg:inline">· {farm.name}</span>}
        {farm && <span className={clsx('chip', farm.status === 'active' ? 'bg-leaf-2/20 text-leaf' : 'bg-line text-fg-2')}>{farm.status}</span>}
        {sandbox && <span className="chip bg-sun/15 text-sun" title="You are looking at the simulation sandbox, not the real-time farm">sandbox</span>}
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-3 whitespace-nowrap xl:gap-4">
        <WorldClock />
        <span className="h-4 w-px bg-line" />
        <span className="flex items-center gap-1.5" title="Dashboard link"><Dot color={connected ? 'var(--color-leaf)' : 'var(--color-alert)'} pulse={connected} />link</span>
        <span className="flex items-center gap-1.5" title={`Simulation bridge: ${bridge?.mode ?? '—'}`}><Radio size={12} style={{ color: bridgeTone }} /><span className="mono hidden lg:inline">{bridge?.ros_connected ? 'ROS 2 · Webots' : bridge?.mode === 'headless' ? 'headless twin' : bridge?.mode ?? '—'}</span></span>
        <span className="mono hidden items-center gap-1.5 md:flex" title="telemetry frames/s"><Satellite size={12} className="text-sky" />{rate} Hz</span>
        <span className="mono hidden items-center gap-1.5 md:flex" title="robots active / cap"><Activity size={12} className="text-leaf" />{active}/2</span>
        {(view === 'ops' || view === 'plan') && <span className="mono hidden items-center gap-1.5 lg:flex" title="map fps"><Gauge size={12} className={fps >= 55 ? 'text-leaf' : fps >= 30 ? 'text-sun' : 'text-alert'} />{fps}</span>}
        <span className="mono hidden items-center gap-1.5 xl:flex" title="telemetry rows this session"><Database size={12} className="text-fg-3" />{bridge?.telemetry ?? 0}</span>
        <span className="hidden xl:inline"><WallClock /></span>
      </div>
    </header>
  )
}

function WallClock() {
  const [t, setT] = useState(() => new Date())
  useEffect(() => { const id = setInterval(() => setT(new Date()), 1000); return () => clearInterval(id) }, [])
  return <span className="mono text-fg-3" title="wall clock">{t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
}
