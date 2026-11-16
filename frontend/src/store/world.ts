/** World-model state pushed over the dashboard socket ('world', 'world_day', 'anomaly', 'forecast', 'analysis'). */
import { create } from 'zustand'
import type { Anomaly, Analysis, Clock, DayWeather, Forecast, Kpi, Ops, WeatherNow, WorldState } from '../lib/world'

interface WorldStore {
  ready: boolean
  clock: Clock | null
  weather: WeatherNow | null
  kpi: Kpi | null
  ops: Ops | null
  anomalies: Anomaly[]
  forecast: Forecast | null
  stages: Record<string, number>
  days: { date: string; kpi: Kpi; weather: DayWeather }[]
  analysis: Record<string, Analysis['status']>
  fleetOps: { op: string; n: number; t: number }[]
  apply: (m: Record<string, unknown>) => void
  setState: (s: WorldState) => void
}

export const useWorld = create<WorldStore>((set) => ({
  ready: false, clock: null, weather: null, kpi: null, ops: null, anomalies: [], forecast: null, stages: {}, days: [], analysis: {}, fleetOps: [],
  setState: (s) => set({ ready: s.ready, clock: s.clock ?? null, weather: s.weather ?? null, kpi: s.kpi ?? null, ops: s.ops ?? null,
    anomalies: s.anomalies ?? [], forecast: s.forecast ?? null, stages: s.stages ?? {} }),
  apply: (m) => {
    switch (m.t) {
      case 'world':
        set({ ready: true, clock: m.clock as Clock, weather: m.weather as WeatherNow, kpi: m.kpi as Kpi, ops: m.ops as Ops,
          anomalies: (m.anomalies as Anomaly[]) ?? [], stages: (m.stages as Record<string, number>) ?? {},
          ...(m.forecast ? { forecast: m.forecast as Forecast } : {}) })
        break
      case 'world_day':
        set((s) => ({ days: [...s.days, { date: m.date as string, kpi: m.summary as Kpi, weather: m.weather as DayWeather }].slice(-400) }))
        break
      case 'forecast':
        set({ forecast: m.forecast as Forecast })
        break
      case 'analysis':
        set((s) => ({ analysis: { ...s.analysis, [m.farm_id as string]: { step: m.step as string, pct: m.pct as number, detail: m.detail as string, done: !!m.done, error: (m.error as string) ?? null } } }))
        break
      case 'sections':
        set((s) => ({ fleetOps: [{ op: m.op as string, n: (m.items as unknown[]).length, t: Date.now() }, ...s.fleetOps].slice(0, 30) }))
        break
      default:
        break
    }
  },
}))
