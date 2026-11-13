import { useLive } from '../store/live'
import { Panel } from '../components/ui'
import { fmt } from '../lib/format'

const ICON: Record<string, string> = {
  mission_started: '▶', mission_complete: '✔', mission_failed: '✖', seeded: '🌱', weeded: '✂', harvested: '🧺', fault_detected: '⚠', fault_cleared: '🔧',
  tow_attached: '🪝', tow_complete: '🏁', charging_started: '🔌', charged: '🔋', world_loaded: '🌍', incident: '🚨', chaos_injected: '💥', robot_power: '⏻',
}
const COLOR: Record<string, string> = { ERROR: 'text-alert', WARN: 'text-sun', INFO: 'text-fg-2' }

export function FeedPanel() {
  const feed = useLive((s) => s.feed)
  return (
    <Panel title="Event stream" bodyClass="p-0">
      {feed.length === 0 && <p className="p-3 text-xs text-fg-3">Waiting for simulation events…</p>}
      <ul className="divide-y divide-line/60">
        {feed.map((e) => (
          <li key={e.id} className={`flex items-start gap-2 px-3 py-1.5 text-[11px] ${COLOR[e.severity] ?? 'text-fg-2'}`}>
            <span className="w-4 text-center">{ICON[e.kind] ?? '•'}</span>
            <span className="mono text-fg-3">{fmt.time(e.t / 1000)}</span>
            <span className="font-medium">{e.kind}</span>
            {e.robot_id && <span className="mono text-fg-3">{e.robot_id}</span>}
            <span className="mono truncate text-fg-3">{summarize(e.payload, e.h3)}</span>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function summarize(p: Record<string, unknown>, h3?: string | null) {
  const parts: string[] = []
  if (p.kind) parts.push(String(p.kind))
  if (p.fault) parts.push(String(p.fault))
  if (p.kg) parts.push(`${p.kg} kg`)
  if (p.crop) parts.push(String(p.crop))
  if (p.cells_observed) parts.push(`${p.cells_observed} cells`)
  if (p.target) parts.push(`→ ${p.target}`)
  if (p.title) parts.push(String(p.title))
  if (h3) parts.push(h3)
  return parts.join(' · ')
}
