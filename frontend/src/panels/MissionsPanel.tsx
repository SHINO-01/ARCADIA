import { useEffect } from 'react'
import { ListOrdered, Pause, Play, X } from 'lucide-react'
import { api } from '../lib/api'
import { useLive } from '../store/live'
import { useUI } from '../store/ui'
import { Chip, Panel } from '../components/ui'
import { fmt } from '../lib/format'

const TONE: Record<string, 'neutral' | 'ok' | 'warn' | 'bad' | 'info'> = { queued: 'neutral', dispatched: 'info', running: 'ok', done: 'neutral', failed: 'bad', cancelled: 'warn' }

export function MissionsPanel() {
  const missions = useLive((s) => s.missions)
  const paused = useLive((s) => s.dispatchPaused)
  const setMissions = useLive((s) => s.setMissions)
  const notify = useUI((s) => s.notify)
  useEffect(() => { api.missions(undefined, 60).then(setMissions).catch(() => {}) }, [setMissions])
  useEffect(() => { const id = setInterval(() => api.missions(undefined, 60).then(setMissions).catch(() => {}), 10000); return () => clearInterval(id) }, [setMissions])
  const live = missions.filter((m) => ['queued', 'dispatched', 'running'].includes(m.status))
  const done = missions.filter((m) => !['queued', 'dispatched', 'running'].includes(m.status)).slice(0, 20)

  return (
    <Panel title={<span className="flex items-center gap-1.5"><ListOrdered size={12} />Dispatch queue</span>}
      right={<button className={paused ? 'btn btn-primary' : 'btn'} onClick={async () => { await (paused ? api.resumeQueue() : api.pauseQueue()); notify(paused ? 'Automated dispatch resumed' : 'Automated dispatch paused', 'ok') }}>
        {paused ? <><Play size={12} />resume</> : <><Pause size={12} />pause</>}</button>}
      bodyClass="p-2">
      {paused && <div className="mb-2 rounded bg-sun/10 px-2 py-1 text-[10px] text-sun">Celery auto-dispatch is paused. Operator overrides still flow directly to robots.</div>}
      {live.length === 0 && <p className="px-1 py-2 text-xs text-fg-3">Queue idle. The dispatcher polls field state every 5 s.</p>}
      <ul className="space-y-1">
        {live.map((m) => (
          <li key={m.id} className="flex items-center gap-2 rounded-md border border-line bg-panel-2/40 px-2 py-1.5 text-xs">
            <Chip tone={TONE[m.status] ?? 'neutral'}>{m.status}</Chip>
            <span className="font-semibold">{m.kind}</span>
            <span className="mono text-fg-3">{m.robot_id}</span>
            <span className="truncate text-fg-2">{m.note}</span>
            <span className="ml-auto flex items-center gap-1 text-[10px] text-fg-3">{m.source === 'operator' ? <Chip tone="info">operator</Chip> : m.source === 'recovery' ? <Chip tone="bad">recovery</Chip> : null}
              <button className="rounded p-0.5 hover:bg-line" title="Cancel" onClick={() => api.cancelMission(m.id).then(() => notify('Mission cancelled', 'ok'))}><X size={12} /></button></span>
          </li>
        ))}
      </ul>
      {done.length > 0 && <>
        <div className="mt-3 mb-1 px-1 text-[10px] uppercase tracking-wide text-fg-3">History</div>
        <ul className="space-y-0.5">
          {done.map((m) => (
            <li key={m.id} className="flex items-center gap-2 px-1 py-0.5 text-[11px] text-fg-2">
              <Chip tone={TONE[m.status] ?? 'neutral'}>{m.status}</Chip><span>{m.kind}</span><span className="mono text-fg-3">{m.robot_id}</span>
              {m.result && 'kg' in m.result && <span className="mono text-leaf">+{String(m.result.kg)} kg</span>}
              {m.result && 'sections_observed' in m.result && <span className="mono text-sky">{String(m.result.sections_observed)} sections</span>}
              <span className="mono ml-auto text-fg-3">{fmt.time(m.completed_at ?? m.created_at)}</span>
            </li>
          ))}
        </ul>
      </>}
    </Panel>
  )
}
