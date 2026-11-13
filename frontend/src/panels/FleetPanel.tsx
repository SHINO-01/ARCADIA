import { useEffect, useRef, useState } from 'react'
import { Battery, Crosshair, Home, Pause, Play, Power, Radio, Zap } from 'lucide-react'
import { clsx } from 'clsx'
import { api } from '../lib/api'
import { ROBOT_COLOR, ROBOT_LABEL, STATUS_COLOR, type Robot } from '../lib/types'
import { liveRegistry, useLive } from '../store/live'
import { useUI, type DispatchKind } from '../store/ui'
import { Bar, Chip, Dot, Panel, Spark } from '../components/ui'

const DISPATCH_KINDS: Record<string, DispatchKind[]> = {
  scout_uav: ['survey', 'goto'], seeder_ugv: ['seed', 'weed', 'harvest', 'goto'], cropcare_ugv: ['weed', 'seed', 'harvest', 'goto'],
  harvester_ugv: ['harvest', 'weed', 'seed', 'goto'], logistics_agv: ['goto'],
}

export function FleetPanel() {
  const fleet = useLive((s) => s.fleet)
  const { selectedRobot, selectRobot, dispatchArm, armDispatch, notify } = useUI()
  const robots = Object.values(fleet).sort((a, b) => Number(b.active) - Number(a.active) || a.id.localeCompare(b.id))
  const active = robots.filter((r) => r.active).length

  return (
    <Panel title="Fleet" right={<span className="mono text-[10px] text-fg-3">{active} active / {robots.length} total · cap 2</span>} bodyClass="p-2 space-y-2">
      {robots.length === 0 && <p className="p-2 text-xs text-fg-3">No fleet provisioned. Draw a boundary, plan infrastructure, then activate the farm.</p>}
      {robots.map((r) => (
        <RobotCard key={r.id} r={r} selected={selectedRobot === r.id} armed={dispatchArm?.robotId === r.id}
          onSelect={() => selectRobot(selectedRobot === r.id ? null : r.id)}
          onArm={(kind) => armDispatch(dispatchArm?.robotId === r.id && dispatchArm.kind === kind ? null : { robotId: r.id, kind })}
          onHome={async () => { try { await api.returnHome(r.id); notify(`${r.id} recalled to dock`, 'ok') } catch (e) { notify((e as Error).message, 'err') } }}
          onResume={async () => { await api.resumeAuto(r.id); notify(`${r.id} back under automated dispatch`, 'ok') }}
          onPower={async () => { try { await api.power(r.id, !r.active); notify(`${r.id} ${r.active ? 'powered down' : 'powered up'}`, 'ok') } catch (e) { notify((e as Error).message, 'err') } }}
        />
      ))}
    </Panel>
  )
}

function RobotCard({ r, selected, armed, onSelect, onArm, onHome, onResume, onPower }: {
  r: Robot; selected: boolean; armed: boolean; onSelect: () => void; onArm: (k: DispatchKind) => void; onHome: () => void; onResume: () => void; onPower: () => void
}) {
  const color = ROBOT_COLOR[r.type] ?? '#fff'
  const sColor = STATUS_COLOR[r.status] ?? '#a3b8aa'
  const hist = useRef<number[]>([])
  const [, tick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => {
      const live = liveRegistry.get(r.id)
      if (live) { hist.current.push(live.speed); if (hist.current.length > 40) hist.current.shift(); tick((t) => t + 1) }
    }, 500)
    return () => clearInterval(id)
  }, [r.id])
  const live = liveRegistry.get(r.id)
  const battery = live?.battery ?? r.battery_pct
  const diagLevel = r.diagnostics?.level
  const faulted = r.status === 'fault' || r.status === 'stranded'

  return (
    <div className={clsx('rounded-lg border p-2.5 transition-colors', selected ? 'border-leaf-2 bg-leaf-2/10' : 'border-line bg-panel-2/40 hover:border-line-2', !r.active && 'opacity-60')}>
      <button className="flex w-full items-start justify-between text-left" onClick={onSelect}>
        <div className="flex items-center gap-2">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
          <div>
            <div className="text-sm font-semibold leading-tight">{r.name ?? r.id} <span className="mono text-[10px] font-normal text-fg-3">{r.id}</span></div>
            <div className="text-[10px] text-fg-3">{ROBOT_LABEL[r.type] ?? r.type} · home {r.home ?? r.home_dock ?? '—'}</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {diagLevel && diagLevel !== 'OK' && <Chip tone={diagLevel === 'ERROR' ? 'bad' : 'warn'}>{diagLevel}</Chip>}
          {r.manual_override && <Chip tone="info">manual</Chip>}
          <span className="flex items-center gap-1 text-[11px] font-medium" style={{ color: sColor }}><Dot color={sColor} pulse={faulted || r.status === 'active'} />{r.status}</span>
        </div>
      </button>
      <div className="mt-2 grid grid-cols-[auto_1fr_auto] items-center gap-2 text-[10px] text-fg-2">
        <Battery size={12} className={battery < 20 ? 'text-alert' : 'text-fg-3'} />
        <Bar value={battery} />
        <span className="mono w-9 text-right">{Math.round(battery)}%</span>
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[10px] text-fg-3">
        <span className="mono">{live ? `${live.speed.toFixed(2)} m/s` : '— m/s'}{live && live.z > 0.5 ? ` · alt ${live.z.toFixed(1)} m` : ''}</span>
        <Spark data={hist.current} color={color} w={90} h={18} max={Number((r.spec as { max_speed?: number } | undefined)?.max_speed ?? 6)} />
        {(r.latency_ms ?? 0) > 500 && <span className="flex items-center gap-1 text-sun"><Radio size={10} />{Math.round(r.latency_ms!)} ms</span>}
      </div>
      {r.fault && <div className="mono mt-1.5 rounded bg-alert/10 px-2 py-1 text-[10px] text-alert">⚠ {String((r.fault as { fault?: string }).fault)} {JSON.stringify((r.fault as { params?: unknown }).params ?? {})}</div>}
      {selected && (
        <div className="mt-2 flex flex-wrap gap-1.5 border-t border-line pt-2">
          {(DISPATCH_KINDS[r.type] ?? ['goto']).map((k) => (
            <button key={k} className={clsx('btn', armed && useUI.getState().dispatchArm?.kind === k && 'btn-primary')} disabled={!r.active || r.status === 'stranded'} onClick={() => onArm(k)} title="Then click a hex on the map">
              <Crosshair size={12} />{k}
            </button>
          ))}
          <button className="btn" disabled={!r.active} onClick={onHome} title="Recall to charging dock"><Home size={12} />dock</button>
          {r.manual_override ? <button className="btn" onClick={onResume}><Play size={12} />auto</button> : <button className="btn" disabled title="Under automated dispatch"><Pause size={12} />auto</button>}
          <button className="btn" onClick={onPower} title={r.active ? 'Power down' : 'Power up (respects active cap)'}><Power size={12} />{r.active ? 'off' : 'on'}</button>
          {r.type === 'scout_uav' && <span className="ml-auto flex items-center gap-1 text-[10px] text-sun"><Zap size={10} />solar skin</span>}
        </div>
      )}
    </div>
  )
}
