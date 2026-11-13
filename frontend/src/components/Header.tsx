import { Cpu, Database, Gauge, Radio, Satellite, SplitSquareHorizontal } from 'lucide-react'
import { clsx } from 'clsx'
import { useLive } from '../store/live'
import { useUI } from '../store/ui'
import { Dot } from './ui'

export function Header({ fps }: { fps: number }) {
  const connected = useLive((s) => s.connected)
  const bridge = useLive((s) => s.bridge)
  const rate = useLive((s) => s.telemetryRate)
  const { mode, setMode, farm, showWebots, toggleWebots } = useUI()
  const bridgeMode = bridge?.mode ?? '—'
  const bridgeTone = bridge?.ros_connected ? '#7ce38b' : bridgeMode === 'headless' ? '#f5c451' : '#ff4d5e'
  return (
    <header className="flex h-12 items-center gap-4 border-b border-line bg-panel px-4">
      <div className="flex items-center gap-2">
        <svg width="22" height="22" viewBox="0 0 64 64"><polygon points="32,4 56,18 56,46 32,60 8,46 8,18" fill="none" stroke="#7CE38B" strokeWidth="4" /><polygon points="32,18 44,25 44,39 32,46 20,39 20,25" fill="#7CE38B" /></svg>
        <div className="leading-none"><div className="text-sm font-bold tracking-[0.18em]">ARCADIA</div><div className="text-[9px] uppercase tracking-[0.2em] text-fg-3">autonomous agri twin</div></div>
      </div>
      <nav className="ml-4 flex items-center gap-1 rounded-md border border-line bg-bg p-0.5 text-xs">
        {(['draw', 'plan', 'ops'] as const).map((m, i) => (
          <button key={m} disabled={(m === 'plan' && !farm) || (m === 'ops' && farm?.status !== 'active')} onClick={() => setMode(m)}
            className={clsx('rounded px-2.5 py-1 capitalize transition-colors disabled:opacity-30', mode === m ? 'bg-leaf-2/20 text-leaf' : 'text-fg-2 hover:bg-line')}>
            <span className="mono mr-1 text-[10px] text-fg-3">{i + 1}</span>{m === 'ops' ? 'Operations' : m === 'plan' ? 'Planning' : 'Geo-select'}
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-4 text-[11px] text-fg-2">
        <span className="flex items-center gap-1.5" title="Dashboard WebSocket"><Dot color={connected ? '#7ce38b' : '#ff4d5e'} pulse={connected} />ws</span>
        <span className="flex items-center gap-1.5" title={`Bridge mode: ${bridgeMode}`}><Radio size={12} style={{ color: bridgeTone }} /><span className="mono">{bridge?.ros_connected ? 'ROS 2 · rosbridge' : bridgeMode === 'headless' ? 'headless twin' : bridgeMode}</span></span>
        <span className="flex items-center gap-1.5 mono" title="Telemetry frames / s"><Satellite size={12} className="text-sky" />{rate} Hz</span>
        <span className="flex items-center gap-1.5 mono" title="Altara canvas frames / s"><Gauge size={12} className={fps >= 55 ? 'text-leaf' : fps >= 30 ? 'text-sun' : 'text-alert'} />{fps} fps</span>
        <span className="flex items-center gap-1.5" title="Persisted telemetry rows"><Database size={12} className="text-fg-3" /><span className="mono">{bridge?.telemetry ?? 0}</span></span>
        <span className="hidden items-center gap-1.5 xl:flex" title="Hardware profile"><Cpu size={12} className="text-fg-3" />i5-13420H · iGPU · cap 2 robots</span>
        <button className={clsx('btn', showWebots && 'btn-primary')} onClick={toggleWebots} title="Toggle split-screen Webots view"><SplitSquareHorizontal size={12} />3D</button>
      </div>
    </header>
  )
}
