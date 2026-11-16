import { create } from 'zustand'
import type { Farm } from '../lib/types'

export type View = 'plan' | 'ops' | 'twin' | 'alerts' | 'analytics'
export type Mode = 'draw' | 'plan' | 'ops'
export type DispatchKind = 'goto' | 'seed' | 'weed' | 'harvest' | 'survey'
export interface RoutePoint { lat: number; lng: number }

interface UIState {
  view: View
  mode: Mode
  farms: Farm[]
  farm: Farm | null
  selected: string[]                 // RTS-style unit selection (ctrl/shift-click for multi)
  selectedRobot: string | null       // primary selection (last clicked)
  dispatchArm: { robotId: string; kind: DispatchKind } | null
  route: RoutePoint[]                // route being built with shift+right-click
  routeLoop: boolean
  hoverInfo: string | null
  layer: 'plan' | 'state' | 'moisture' | 'health' | 'crop'
  follow: string | null              // 3D camera follow target
  mapSlot: HTMLElement | null        // the active view's map placeholder (map is mounted once in App)
  toast: { msg: string; kind: 'ok' | 'err' | 'info'; id: number } | null
  setView: (v: View) => void
  setMode: (m: Mode) => void
  setFarms: (f: Farm[]) => void
  setFarm: (f: Farm | null) => void
  select: (ids: string[], primary?: string | null) => void
  toggleSelect: (id: string) => void
  selectRobot: (id: string | null) => void
  armDispatch: (a: UIState['dispatchArm']) => void
  addRoutePoint: (p: RoutePoint) => void
  popRoutePoint: () => void
  clearRoute: () => void
  setRouteLoop: (v: boolean) => void
  setHoverInfo: (h: string | null) => void
  setLayer: (l: UIState['layer']) => void
  setFollow: (id: string | null) => void
  setMapSlot: (el: HTMLElement | null) => void
  notify: (msg: string, kind?: 'ok' | 'err' | 'info') => void
}

let toastId = 0
export const useUI = create<UIState>((set) => ({
  view: 'plan', mode: 'draw', farms: [], farm: null, selected: [], selectedRobot: null, dispatchArm: null, route: [], routeLoop: true,
  hoverInfo: null, layer: 'plan', follow: null, mapSlot: null, toast: null,
  setView: (view) => set((s) => ({ view, mode: view === 'plan' ? (s.farm ? 'plan' : 'draw') : view === 'ops' ? 'ops' : s.mode })),
  setMode: (mode) => set({ mode }),
  setFarms: (farms) => set({ farms }),
  setFarm: (farm) => set({ farm }),
  select: (selected, primary) => set({ selected, selectedRobot: primary === undefined ? (selected[selected.length - 1] ?? null) : primary }),
  toggleSelect: (id) => set((s) => {
    const selected = s.selected.includes(id) ? s.selected.filter((x) => x !== id) : [...s.selected, id]
    return { selected, selectedRobot: selected.includes(id) ? id : (selected[selected.length - 1] ?? null) }
  }),
  selectRobot: (id) => set({ selected: id ? [id] : [], selectedRobot: id }),
  armDispatch: (dispatchArm) => set({ dispatchArm }),
  addRoutePoint: (p) => set((s) => ({ route: [...s.route, p] })),
  popRoutePoint: () => set((s) => ({ route: s.route.slice(0, -1) })),
  clearRoute: () => set({ route: [] }),
  setRouteLoop: (routeLoop) => set({ routeLoop }),
  setHoverInfo: (hoverInfo) => set({ hoverInfo }),
  setLayer: (layer) => set({ layer }),
  setFollow: (follow) => set({ follow }),
  setMapSlot: (mapSlot) => set({ mapSlot }),
  notify: (msg, kind = 'info') => {
    const id = ++toastId
    set({ toast: { msg, kind, id } })
    setTimeout(() => set((s) => (s.toast?.id === id ? { toast: null } : {})), 3200)
  },
}))
