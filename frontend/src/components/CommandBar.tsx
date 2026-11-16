/** RTS-style unit command bar: appears when units are selected (bottom of the map). */
import { Crosshair, Home, Loader, Play, Repeat, Route, Square, Trash2, X, Zap } from 'lucide-react'
import { clsx } from 'clsx'
import { api } from '../lib/api'
import { ROBOT_COLOR, ROBOT_LABEL } from '../lib/types'
import { liveRegistry, useLive } from '../store/live'
import { useUI, type DispatchKind } from '../store/ui'
import { Bar } from './ui'

const TASKS: Record<string, DispatchKind[]> = { scout_uav: ['survey'], seeder_ugv: ['seed', 'weed', 'harvest'], cropcare_ugv: ['weed', 'seed', 'harvest'], harvester_ugv: ['harvest', 'weed', 'seed'], logistics_agv: [] }

export function CommandBar() {
  const { selected, selectedRobot, dispatchArm, armDispatch, route, routeLoop, setRouteLoop, clearRoute, popRoutePoint, select, notify } = useUI()
  const fleet = useLive((s) => s.fleet)
  if (!selected.length) return null
  const units = selected.map((id) => fleet[id]).filter(Boolean)
  const prim = selectedRobot ? fleet[selectedRobot] : units[0]
  const kinds = prim ? TASKS[prim.type] ?? [] : []
  const run = async (label: string, fn: (id: string) => Promise<unknown>) => {
    try { await Promise.all(selected.map(fn)); notify(`${label} → ${selected.join(', ')}`, 'ok') } catch (e) { notify((e as Error).message, 'err') }
  }
  const sendRoute = async (loop: boolean) => {
    if (route.length < 1) return notify('Shift+right-click on the map to add route waypoints first', 'info')
    await run(loop ? 'Patrol route' : 'Route', (id) => api.route(id, { waypoints: route, loop, kind: loop ? 'patrol' : 'goto' }))
    clearRoute()
  }
  return (
    <div className="pointer-events-auto absolute bottom-3 left-3 right-3 z-10 flex items-stretch justify-center gap-2 overflow-x-auto rounded-xl border border-line-2 bg-panel/95 p-2 shadow-2xl backdrop-blur">
      {/* selected units */}
      <div className="flex items-center gap-1.5 border-r border-line pr-2">
        {units.map((u) => { const live = liveRegistry.get(u.id); const b = live?.battery ?? u.battery_pct; return (
          <div key={u.id} className={clsx('w-[118px] shrink-0 rounded-md border px-2 py-1.5', u.id === selectedRobot ? 'border-leaf-2 bg-leaf-2/10' : 'border-line bg-panel-2/60')}>
            <div className="flex items-center justify-between text-[11px]"><span className="flex items-center gap-1.5 font-semibold"><span className="h-2 w-2 rounded-sm" style={{ background: ROBOT_COLOR[u.type] }} />{u.id}</span><span className="text-[9px] uppercase text-fg-3">{u.status}</span></div>
            <div className="text-[9px] text-fg-3">{ROBOT_LABEL[u.type]}</div>
            <div className="mt-1 flex items-center gap-1.5"><Bar value={b} /><span className="mono text-[9px] text-fg-2">{Math.round(b)}%</span></div>
          </div>) })}
      </div>
      {/* orders */}
      <div className="grid grid-cols-4 gap-1">
        <Cmd icon={<Crosshair size={13} />} label="Move" hint="right-click map" active={dispatchArm?.kind === 'goto'} onClick={() => prim && armDispatch(dispatchArm?.kind === 'goto' ? null : { robotId: prim.id, kind: 'goto' })} />
        {kinds.slice(0, 3).map((k) => <Cmd key={k} icon={<Zap size={13} />} label={k[0].toUpperCase() + k.slice(1)} hint="click a bed" active={dispatchArm?.kind === k} onClick={() => prim && armDispatch(dispatchArm?.kind === k ? null : { robotId: prim.id, kind: k })} />)}
        <Cmd icon={<Square size={13} />} label="Stop" hint="S" tone="warn" onClick={() => run('Stop', api.stop)} />
        <Cmd icon={<Home size={13} />} label="Dock" hint="H" onClick={() => run('Return to dock', api.returnHome)} />
        <Cmd icon={<Loader size={13} />} label="Auto" hint="hand back" onClick={() => run('Automated dispatch', api.resumeAuto)} />
        <Cmd icon={<X size={13} />} label="Deselect" hint="Esc" onClick={() => select([])} />
      </div>
      {/* route builder */}
      <div className="flex w-[180px] shrink-0 flex-col justify-between border-l border-line pl-2 text-[10px]">
        <div className="flex items-center justify-between text-fg-2"><span className="flex items-center gap-1 font-semibold"><Route size={12} />Route builder</span><span className="mono text-fg-3">{route.length} wp</span></div>
        <div className="text-fg-3">shift + right-click to add waypoints</div>
        <div className="flex items-center gap-1">
          <button className={clsx('btn', routeLoop && 'btn-primary')} onClick={() => setRouteLoop(!routeLoop)} title="Loop as patrol"><Repeat size={12} />loop</button>
          <button className="btn btn-primary" disabled={!route.length} onClick={() => sendRoute(routeLoop)}><Play size={12} />{routeLoop ? 'Patrol' : 'Go'}</button>
          <button className="btn" disabled={!route.length} onClick={popRoutePoint} title="Undo last waypoint">↶</button>
          <button className="btn" disabled={!route.length} onClick={clearRoute} title="Clear route"><Trash2 size={12} /></button>
        </div>
      </div>
    </div>
  )
}
function Cmd({ icon, label, hint, onClick, active, tone }: { icon: React.ReactNode; label: string; hint?: string; onClick: () => void; active?: boolean; tone?: 'warn' }) {
  return <button onClick={onClick} className={clsx('flex h-[50px] w-[58px] shrink-0 flex-col items-center justify-center gap-0.5 rounded-md border text-[10px] transition-colors',
    active ? 'border-leaf-2 bg-leaf-2/20 text-leaf' : tone === 'warn' ? 'border-line bg-panel-2/60 text-sun hover:bg-sun/10' : 'border-line bg-panel-2/60 text-fg hover:bg-line')}>
    {icon}<span>{label}</span>{hint && <span className="text-[8px] text-fg-3">{hint}</span>}</button>
}
