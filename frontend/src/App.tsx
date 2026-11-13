import { useEffect, useState } from 'react'
import { FarmMap } from './components/FarmMap'
import { Header } from './components/Header'
import { WebotsView } from './components/WebotsView'
import { PlanningPanel } from './panels/PlanningPanel'
import { FleetPanel } from './panels/FleetPanel'
import { MissionsPanel } from './panels/MissionsPanel'
import { IncidentModal, IncidentsPanel } from './panels/IncidentsPanel'
import { EnergyPanel } from './panels/EnergyPanel'
import { ChaosPanel } from './panels/ChaosPanel'
import { FeedPanel } from './panels/FeedPanel'
import { useLive } from './store/live'
import { useUI } from './store/ui'
import { clsx } from 'clsx'

export default function App() {
  const connect = useLive((s) => s.connect)
  const { mode, showWebots, toast, armDispatch, dispatchArm, farm } = useUI()
  const [drawn, setDrawn] = useState<{ feature: GeoJSON.Feature<GeoJSON.Polygon>; areaM2: number } | null>(null)
  const [fps, setFps] = useState(0)

  useEffect(() => connect(), [connect])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && dispatchArm) armDispatch(null)
      if (e.key === '1') useUI.getState().setMode('draw')
      if (e.key === '2' && farm) useUI.getState().setMode('plan')
      if (e.key === '3' && farm?.status === 'active') useUI.getState().setMode('ops')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dispatchArm, armDispatch, farm])

  const ops = mode === 'ops'
  return (
    <div className="relative flex h-full flex-col">
      <Header fps={fps} />
      <main className="grid min-h-0 flex-1 gap-2 p-2" style={{ gridTemplateColumns: ops ? '300px 1fr 320px' : '340px 1fr' }}>
        {/* left rail */}
        <aside className="flex min-h-0 flex-col gap-2">
          <PlanningPanel drawn={drawn} />
          {ops && <FleetPanel />}
        </aside>

        {/* centre: split-screen map + Webots */}
        <section className="flex min-h-0 flex-col gap-2">
          <div className={clsx('grid min-h-0 flex-1 gap-2', ops && showWebots ? 'grid-rows-[3fr_2fr] xl:grid-cols-[3fr_2fr] xl:grid-rows-1' : 'grid-rows-1')}>
            <div className="min-h-0 overflow-hidden rounded-lg border border-line"><FarmMap onDrawn={(feature, areaM2) => setDrawn({ feature, areaM2 })} onFps={setFps} /></div>
            {ops && showWebots && <div className="min-h-0"><WebotsView /></div>}
          </div>
          {ops && <div className="h-44 shrink-0"><FeedPanel /></div>}
        </section>

        {/* right rail */}
        {ops && (
          <aside className="flex min-h-0 flex-col gap-2">
            <IncidentsPanel />
            <MissionsPanel />
            <EnergyPanel />
            <ChaosPanel />
          </aside>
        )}
      </main>
      <IncidentModal />
      {toast && (
        <div className={clsx('anim-slide-up absolute bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-md border px-3 py-2 text-xs shadow-xl',
          toast.kind === 'ok' && 'border-leaf-2 bg-panel text-leaf', toast.kind === 'err' && 'border-alert bg-panel text-alert', toast.kind === 'info' && 'border-line-2 bg-panel text-fg')}>{toast.msg}</div>
      )}
    </div>
  )
}
