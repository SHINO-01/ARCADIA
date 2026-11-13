import { useEffect } from 'react'
import { AlertTriangle, Check, Truck, Wrench } from 'lucide-react'
import { api } from '../lib/api'
import { useLive } from '../store/live'
import { useUI } from '../store/ui'
import { Chip, Panel } from '../components/ui'
import { fmt } from '../lib/format'
import type { Incident } from '../lib/types'

const STATE_TONE: Record<string, 'neutral' | 'ok' | 'warn' | 'bad' | 'info'> = { open: 'bad', acknowledged: 'warn', recovering: 'info', resolved: 'neutral' }

export function IncidentsPanel() {
  const incidents = useLive((s) => s.incidents)
  const setIncidents = useLive((s) => s.setIncidents)
  const notify = useUI((s) => s.notify)
  useEffect(() => { api.incidents().then(setIncidents).catch(() => {}) }, [setIncidents])
  const open = incidents.filter((i) => i.state !== 'resolved')
  const resolved = incidents.filter((i) => i.state === 'resolved').slice(0, 10)
  return (
    <Panel title={<span className="flex items-center gap-1.5"><AlertTriangle size={12} />Maintenance &amp; alerts</span>}
      right={open.length > 0 ? <Chip tone="bad">{open.length} open</Chip> : <Chip tone="ok">all clear</Chip>} bodyClass="p-2">
      {open.length === 0 && <p className="px-1 py-2 text-xs text-fg-3">No active incidents. The Chaos Engine injects faults on its configured interval.</p>}
      <ul className="space-y-1.5">
        {open.map((i) => <IncidentRow key={i.id} i={i} notify={notify} />)}
      </ul>
      {resolved.length > 0 && <>
        <div className="mt-3 mb-1 px-1 text-[10px] uppercase tracking-wide text-fg-3">Resolved</div>
        <ul className="space-y-0.5">{resolved.map((i) => (
          <li key={i.id} className="flex items-center gap-2 px-1 py-0.5 text-[11px] text-fg-2"><Check size={11} className="text-leaf" /><span className="mono text-fg-3">{i.robot_id}</span><span className="truncate">{i.title}</span><span className="mono ml-auto text-fg-3">{i.resolved_at ? fmt.time(i.resolved_at) : ''}</span></li>
        ))}</ul>
      </>}
    </Panel>
  )
}

export function IncidentRow({ i, notify }: { i: Incident; notify: (m: string, k?: 'ok' | 'err' | 'info') => void }) {
  return (
    <li className="rounded-md border border-line bg-panel-2/40 p-2 text-xs">
      <div className="flex items-center gap-2">
        <Chip tone={STATE_TONE[i.state] ?? 'neutral'}>{i.state}</Chip>
        <span className="mono text-fg-3">{i.robot_id}</span>
        <span className="font-semibold">{i.kind}</span>
        {i.requires_tow && <Chip tone="warn"><Truck size={10} />tow</Chip>}
        <span className="mono ml-auto text-[10px] text-fg-3">{fmt.time(i.created_at)}</span>
      </div>
      <div className="mt-1 text-fg-2">{i.title}</div>
      {i.detail && <div className="mono mt-1 truncate text-[10px] text-fg-3">{JSON.stringify((i.detail as { params?: unknown }).params ?? i.detail)}</div>}
      {i.state !== 'resolved' && i.state !== 'recovering' && (
        <div className="mt-2 flex gap-1.5">
          {i.state === 'open' && <button className="btn" onClick={() => api.ackIncident(i.id).then(() => notify('Acknowledged', 'ok'))}><Check size={12} />ack</button>}
          <button className="btn btn-primary" onClick={() => api.recoverIncident(i.id).then(() => notify(i.requires_tow ? 'Logistics AGV dispatched for tow recovery' : 'Remote subsystem reset issued', 'ok')).catch((e) => notify(e.message, 'err'))}>
            {i.requires_tow ? <><Truck size={12} />dispatch tow</> : <><Wrench size={12} />remote reset</>}
          </button>
        </div>
      )}
      {i.state === 'recovering' && <div className="mt-1.5 text-[10px] text-sky">Recovery protocol running{i.recovery_mission_id ? ` · mission ${fmt.short(i.recovery_mission_id)}` : ''}</div>}
    </li>
  )
}

/** Interruptive modal triggered by new Chaos Engine incidents. */
export function IncidentModal() {
  const inc = useLive((s) => s.pendingModal)
  const dismiss = useLive((s) => s.dismissModal)
  const notify = useUI((s) => s.notify)
  if (!inc) return null
  const act = async (recover: boolean) => {
    try {
      if (recover) { await api.recoverIncident(inc.id); notify(inc.requires_tow ? 'Logistics AGV dispatched — tow recovery in progress' : 'Remote reset issued', 'ok') }
      else await api.ackIncident(inc.id)
    } catch (e) { notify((e as Error).message, 'err') }
    dismiss()
  }
  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-bg/70 backdrop-blur-sm" role="dialog" aria-modal="true">
      <div className="anim-slide-up w-[440px] rounded-xl border border-alert/50 bg-panel p-5 shadow-2xl">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-alert/20 text-alert"><AlertTriangle size={20} /></span>
          <div>
            <div className="text-[10px] uppercase tracking-[0.14em] text-alert">Chaos engine · {inc.severity}</div>
            <h3 className="text-base font-semibold">{inc.title}</h3>
          </div>
        </div>
        <div className="mono mt-3 rounded-md bg-bg px-3 py-2 text-xs text-fg-2">
          <div>unit <span className="text-fg">{inc.robot_id}</span> · fault <span className="text-fg">{inc.kind}</span></div>
          {inc.detail && <div className="mt-1 truncate text-fg-3">{JSON.stringify((inc.detail as { params?: unknown }).params ?? {})}</div>}
          {inc.detail && 'battery_pct' in inc.detail && <div className="text-fg-3">battery {Number(inc.detail.battery_pct).toFixed(1)}% at ENU {Number((inc.detail as { x?: number }).x ?? 0).toFixed(1)}, {Number((inc.detail as { y?: number }).y ?? 0).toFixed(1)}</div>}
        </div>
        <p className="mt-3 text-xs text-fg-2">
          {inc.requires_tow
            ? 'The unit is stranded in the field. Dispatch the Logistics AGV to winch it back to its Renewable Charging Dock for repair and recharge.'
            : 'The unit is degraded but mobile. A remote subsystem reset will clear the fault without a tow.'}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn" onClick={() => act(false)}>Acknowledge only</button>
          <button className={inc.requires_tow ? 'btn btn-danger' : 'btn btn-primary'} onClick={() => act(true)}>{inc.requires_tow ? <><Truck size={12} />Dispatch tow AGV</> : <><Wrench size={12} />Remote reset</>}</button>
        </div>
      </div>
    </div>
  )
}
