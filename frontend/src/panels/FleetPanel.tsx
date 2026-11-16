import { useEffect, useState } from 'react'
import { Battery, Play, Power, Radio } from 'lucide-react'
import { clsx } from 'clsx'
import { api } from '../lib/api'
import { ROBOT_COLOR, ROBOT_LABEL, STATUS_COLOR, type Robot } from '../lib/types'
import { liveRegistry, useLive } from '../store/live'
import { useUI } from '../store/ui'
import { Bar, Chip, Dot, Panel, Spark } from '../components/ui'

export function FleetPanel() {
  const fleet = useLive((s) => s.fleet)
  const { selectedRobot, selectRobot, notify } = useUI()
  const robots = Object.values(fleet).sort((a, b) => Number(b.active) - Number(a.active) || a.id.localeCompare(b.id))
  const active = robots.filter((r) => r.active).length

  return (
    <Panel title="Fleet" right={<span className="mono text-[10px] text-fg-3">{active} active / {robots.length} total · cap 2</span>} bodyClass="p-2 space-y-2">
      {robots.length === 0 && <p className="p-2 text-xs text-fg-3">No fleet provisioned. Draw a boundary, plan infrastructure, then activate the farm.</p>}
      {robots.map((r) => (
        <RobotCard key={r.id} r={r} selected={selectedRobot === r.id}
          onSelect={() => selectRobot(selectedRobot === r.id ? null : r.id)}
          onResume={async () => { await api.resumeAuto(r.id); notify(`${r.id} back under automated dispatch`, 'ok') }}
          onPower={async () => { try { await api.power(r.id, !r.active); notify(`${r.id} ${r.active ? 'powered down' : 'powered up'}`, 'ok') } catch (e) { notify((e as Error).message, 'err') } }}
        />
      ))}
    </Panel>
  )
}

function RobotCard({ r, selected, onSelect, onResume, onPower }: {
  r: Robot; selected: boolean; onSelect: () => void; onResume: () => void; onPower: () => void
}) {
  const color = ROBOT_COLOR[r.type] ?? '#fff'
  const sColor = STATUS_COLOR[r.status] ?? '#a3b8aa'
  const [hist, setHist] = useState<number[]>([])
  useEffect(() => {
    const id = setInterval(() => {
      const live = liveRegistry.get(r.id)
      if (live) setHist((h) => [...h, live.speed].slice(-40))
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
        <Spark data={hist} color={color} w={90} h={18} max={Number((r.spec as { max_speed?: number } | undefined)?.max_speed ?? 6)} />
        {(r.latency_ms ?? 0) > 500 && <span className="flex items-center gap-1 text-sun"><Radio size={10} />{Math.round(r.latency_ms!)} ms</span>}
      </div>
      {r.fault && <div className="mono mt-1.5 rounded bg-alert/10 px-2 py-1 text-[10px] text-alert">⚠ {String((r.fault as { fault?: string }).fault)} {JSON.stringify((r.fault as { params?: unknown }).params ?? {})}</div>}
      {selected && (
        <div className="mt-2 flex items-center gap-1.5 border-t border-line pt-2 text-[10px] text-fg-3">
          <span>orders in the command bar below the map</span>
          {r.manual_override ? <button className="btn ml-auto" onClick={onResume}><Play size={12} />auto</button> : null}
          <button className={r.manual_override ? 'btn' : 'btn ml-auto'} onClick={onPower} title={r.active ? 'Power down' : 'Power up (respects active cap)'}><Power size={12} />{r.active ? 'off' : 'on'}</button>
        </div>
      )}
    </div>
  )
}
