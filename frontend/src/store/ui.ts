import { create } from 'zustand'
import type { Farm } from '../lib/types'

export type Mode = 'draw' | 'plan' | 'ops'
export type DispatchKind = 'goto' | 'seed' | 'weed' | 'harvest' | 'survey'

interface UIState {
  mode: Mode
  farms: Farm[]
  farm: Farm | null
  selectedRobot: string | null
  dispatchArm: { robotId: string; kind: DispatchKind } | null
  hoverHex: string | null
  showWebots: boolean
  layer: 'state' | 'moisture' | 'health' | 'crop'
  toast: { msg: string; kind: 'ok' | 'err' | 'info'; id: number } | null
  setMode: (m: Mode) => void
  setFarms: (f: Farm[]) => void
  setFarm: (f: Farm | null) => void
  selectRobot: (id: string | null) => void
  armDispatch: (a: UIState['dispatchArm']) => void
  setHoverHex: (h: string | null) => void
  toggleWebots: () => void
  setLayer: (l: UIState['layer']) => void
  notify: (msg: string, kind?: 'ok' | 'err' | 'info') => void
}

let toastId = 0
export const useUI = create<UIState>((set) => ({
  mode: 'draw', farms: [], farm: null, selectedRobot: null, dispatchArm: null, hoverHex: null, showWebots: true, layer: 'state', toast: null,
  setMode: (mode) => set({ mode }),
  setFarms: (farms) => set({ farms }),
  setFarm: (farm) => set({ farm }),
  selectRobot: (selectedRobot) => set({ selectedRobot }),
  armDispatch: (dispatchArm) => set({ dispatchArm }),
  setHoverHex: (hoverHex) => set({ hoverHex }),
  toggleWebots: () => set((s) => ({ showWebots: !s.showWebots })),
  setLayer: (layer) => set({ layer }),
  notify: (msg, kind = 'info') => {
    const id = ++toastId
    set({ toast: { msg, kind, id } })
    setTimeout(() => set((s) => (s.toast?.id === id ? { toast: null } : {})), 3200)
  },
}))
