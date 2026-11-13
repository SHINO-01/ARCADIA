import { useEffect, useRef, useState } from 'react'
import { Sun, Wind } from 'lucide-react'
import { useLive } from '../store/live'
import { Bar, Panel, Spark } from '../components/ui'
import { fmt } from '../lib/format'

export function EnergyPanel() {
  const docks = useLive((s) => s.docks)
  const hist = useRef<Record<string, number[]>>({})
  const [, tick] = useState(0)
  const list = Object.values(docks).sort((a, b) => a.slug.localeCompare(b.slug))
  useEffect(() => {
    for (const d of list) { const h = (hist.current[d.slug] ??= []); h.push(d.generated_kw); if (h.length > 60) h.shift() }
    tick((t) => t + 1)
  }, [docks]) // eslint-disable-line react-hooks/exhaustive-deps
  const first = list[0]
  const hour = first?.sim_hour ?? 12
  const night = hour < 6 || hour > 18
  return (
    <Panel title="Renewable microgrids" right={first && <span className="mono text-[10px] text-fg-3">{night ? '☾' : '☀'} {fmt.hour(hour)} sim · wind {first.wind_ms.toFixed(1)} m/s</span>} bodyClass="p-2 space-y-2">
      {list.length === 0 && <p className="px-1 py-2 text-xs text-fg-3">No docks yet. Plan the farm to place solar/wind microgrids.</p>}
      {list.map((d) => (
        <div key={d.slug} className="rounded-md border border-line bg-panel-2/40 p-2 text-xs">
          <div className="flex items-center gap-2">
            {d.kind === 'solar_dock' ? <Sun size={13} className="text-sun" /> : <Wind size={13} className="text-sky" />}
            <span className="font-semibold">{d.slug}</span>
            <span className="text-fg-3">{d.kind === 'solar_dock' ? 'solar' : 'wind'} microgrid</span>
            <span className="mono ml-auto text-fg-2">{fmt.kw(d.generated_kw)} <span className="text-fg-3">in</span> · {fmt.kw(d.consumed_kw)} <span className="text-fg-3">out</span></span>
          </div>
          <div className="mt-1.5 grid grid-cols-[1fr_auto] items-center gap-2">
            <Bar value={d.soc * 100} color={d.soc < 0.2 ? '#ff4d5e' : d.kind === 'solar_dock' ? '#f5c451' : '#5cc8ff'} />
            <span className="mono text-[10px] text-fg-2">{fmt.kwh(d.stored_kwh)} / {fmt.kwh(d.storage_kwh)}</span>
          </div>
          <div className="mt-1 flex items-center justify-between text-[10px] text-fg-3">
            <span>{d.kind === 'solar_dock' ? `irradiance ${(d.irradiance * 100).toFixed(0)}%` : `turbine ${(d.generated_kw / Math.max(0.01, (d as { capacity_kw?: number }).capacity_kw ?? d.generated_kw) * 100 || 0).toFixed(0)}%`}</span>
            <Spark data={hist.current[d.slug] ?? []} color={d.kind === 'solar_dock' ? '#f5c451' : '#5cc8ff'} w={100} h={18} />
          </div>
        </div>
      ))}
    </Panel>
  )
}
