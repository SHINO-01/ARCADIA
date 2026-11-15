import { useEffect, useRef, useState } from 'react'
import { clsx } from 'clsx'
import { FarmMap, type FarmMapHandle } from './components/FarmMap'
import { CommandBar } from './components/CommandBar'
import { Legend } from './components/Legend'
import { NavRail, StatusBar } from './components/Shell'
import { IncidentModal } from './panels/IncidentsPanel'
import { AlertsView, AnalyticsView, OpsView, PlanView, TwinView } from './views/views'
import { api } from './lib/api'
import { useLive } from './store/live'
import { useUI } from './store/ui'

export default function App() {
  const connect = useLive((s) => s.connect)
  const { view, setView, toast, dispatchArm, armDispatch, farm, selected, select, clearRoute, route, popRoutePoint, notify, mapSlot } = useUI()
  const [drawn, setDrawn] = useState<{ feature: GeoJSON.Feature<GeoJSON.Polygon>; areaM2: number } | null>(null)
  const [fps, setFps] = useState(0)
  const mapRef = useRef<FarmMapHandle>(null)
  const mainRef = useRef<HTMLElement>(null)
  const [rect, setRect] = useState<{ top: number; left: number; width: number; height: number } | null>(null)

  useEffect(() => connect(), [connect])

  // keyboard: 1-5 views, Esc, S stop, H dock, Backspace undo waypoint
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
      if (e.key === 'Escape') { if (dispatchArm) armDispatch(null); else if (route.length) clearRoute(); else select([]) }
      const views = { '1': 'plan', '2': 'ops', '3': 'twin', '4': 'alerts', '5': 'analytics' } as const
      if (e.key in views) {
        const v = views[e.key as keyof typeof views]
        const live = useUI.getState().farms.find((f) => f.status === 'active')
        if (v === 'plan') setView(v)
        else if (live) { if (farm?.id !== live.id) useUI.getState().setFarm(live); setView(v) }
      }
      if (selected.length && view === 'ops') {
        if (e.key === 's' || e.key === 'S') Promise.all(selected.map(api.stop)).then(() => notify('Stop', 'ok'))
        if (e.key === 'h' || e.key === 'H') Promise.all(selected.map(api.returnHome)).then(() => notify('Return to dock', 'ok'))
        if (e.key === 'Backspace' && route.length) popRoutePoint()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dispatchArm, armDispatch, farm, selected, select, view, setView, route, clearRoute, popRoutePoint, notify])

  // Keep the single map instance glued to the active view placeholder (MapSlot).
  const mapVisible = (view === 'plan' || view === 'ops') && !!mapSlot
  useEffect(() => {
    if (!mapSlot || !mainRef.current) { setRect(null); return }
    const sync = () => {
      const m = mainRef.current!.getBoundingClientRect(); const r = mapSlot.getBoundingClientRect()
      setRect({ top: r.top - m.top, left: r.left - m.left, width: r.width, height: r.height })
    }
    sync()
    const ro = new ResizeObserver(sync); ro.observe(mapSlot); ro.observe(mainRef.current)
    window.addEventListener('resize', sync)
    return () => { ro.disconnect(); window.removeEventListener('resize', sync) }
  }, [mapSlot, view])

  return (
    <div className="flex h-full">
      <NavRail />
      <div className="flex min-w-0 flex-1 flex-col">
        <StatusBar fps={fps} />
        <main ref={mainRef} className="relative min-h-0 flex-1 bg-bg">
          {view === 'plan' && <PlanView drawn={drawn} onStartDraw={() => mapRef.current?.startDraw()} onClearDraw={() => { mapRef.current?.clearDraw(); setDrawn(null) }} />}
          {view === 'ops' && <OpsView />}
          {view === 'twin' && <TwinView />}
          {view === 'alerts' && <AlertsView />}
          {view === 'analytics' && <AnalyticsView />}
          {/* Single map instance positioned over the active view's MapSlot; never re-mounted */}
          <div className={clsx('absolute overflow-hidden rounded-lg', !mapVisible && 'pointer-events-none opacity-0')}
            style={rect && mapVisible ? { top: rect.top, left: rect.left, width: rect.width, height: rect.height } : { top: 0, left: 0, width: 2, height: 2 }}>
            <FarmMap ref={mapRef} visible={mapVisible} onDrawn={(feature, areaM2) => setDrawn({ feature, areaM2 })} onFps={setFps} />
            {view === 'plan' && farm?.plan && <div className="pointer-events-none absolute bottom-3 left-3"><Legend /></div>}
            {view === 'ops' && farm?.plan && <div className="pointer-events-none absolute left-3 top-3"><Legend compact /></div>}
            {view === 'ops' && <CommandBar />}
            {view === 'ops' && <div className="pointer-events-none absolute right-12 top-3 rounded-md bg-bg/80 px-2 py-1 text-[10px] text-fg-3">click unit · ctrl+click multi · right-click move · shift+right-click waypoint</div>}
          </div>
        </main>
      </div>
      <IncidentModal />
      {toast && <div className={clsx('anim-slide-up absolute bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-md border px-3 py-2 text-xs shadow-xl',
        toast.kind === 'ok' && 'border-leaf-2 bg-panel text-leaf', toast.kind === 'err' && 'border-alert bg-panel text-alert', toast.kind === 'info' && 'border-line-2 bg-panel text-fg')}>{toast.msg}</div>}
    </div>
  )
}
