/** Agronomic world model: types and API (backend app/api/world.py, app/api/farms.py setup endpoints). */
const BASE = '/api'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!res.ok) {
    let detail: unknown = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* ignore */ }
    if (Array.isArray(detail)) detail = detail.slice(0, 3).map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}`).join(' · ')
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
  }
  return res.json() as Promise<T>
}
const post = <T,>(path: string, body?: unknown) => req<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export interface Q { p10: number; p50: number; p90: number }
export interface Clock { date: string; hour: number; doy: number; day_seconds: number; paused: boolean; crop: string; crop_name: string; sow_months: number[] }
export interface WeatherNow { t: number; irradiance_wm2: number; rh: number; wind: number; rain_today: number; cloud: number; et0: number; tmax: number; tmin: number; hail: boolean; scenarios: string[] }
export interface Kpi {
  date: string; lai: number; biomass_t_ha: number; yield_pot_t: number; moisture: number; ks: number; irrigation_m3: number; weed: number
  disease: number; waterlogged_pct: number; n_soil: number; crop_pct: number; ndvi: number; stage_counts: Record<string, number>
  irr_demand_mm?: number; irr_delivered_mm?: number
}
export interface Ops { units: number; hours_per_day: number; workable: boolean; pending: Record<string, number>; pending_ha: Record<string, number>; done_today: Record<string, number>; season_ha: Record<string, number> }
export interface Anomaly { key: string; kind: string; severity: 'info' | 'warning' | 'critical'; title: string; detail: string; method: string; value: number; threshold: number; action: string; sections: string[]; since?: string; updated?: string }
export interface Forecast { members?: number; planted_sections?: number; planted_ha?: number; yield_t?: Q; yield_t_ha?: Q; revenue_usd?: Q; maturity_date?: { p10?: string; p50?: string; p90?: string }; irrigation_mm?: Q; spread_cv?: number }
export interface Outlook { date: string; tmax: Q; tmin: Q; rain_mm: Q; rain_prob: number; et0: Q; wind: Q }
export interface Scenario { id: string; kind: string; start: number; days: number; intensity: number; label: string }
export interface WorldState {
  ready: boolean; farm_id?: string; clock?: Clock; weather?: WeatherNow; kpi?: Kpi; ops?: Ops; anomalies?: Anomaly[]; scenarios?: Scenario[]
  irrigation?: { enabled: boolean; mad: number; capacity_mm: number; efficiency: number; failed: boolean }; outlook?: Outlook[]; forecast?: Forecast | null
  climate?: { source: string; years: number; elevation: number; annual_rain_mm: number; annual_tmean: number }
  soil?: { source: string; texture: string; taw_mm_per_m: number; relief_m: number; [k: string]: unknown }
  crop?: { key: string; name: string; [k: string]: unknown }
  stages?: Record<string, number>
}
export interface DayWeather { date: string; tmax: number; tmin: number; tmean: number; rain: number; rs: number; rh: number; wind: number; et0: number; cloud: number; scenarios: string[] }
export interface CropOption { crop: string; name: string; sow_date: string; season_days: number; yield_t_ha: Q; margin_usd_ha: Q; revenue_usd_ha: Q; risk_cv: number; failure_prob: number; water_stress_days: number; irrigation_mm: number; recommended: boolean }
export interface Analysis {
  status: { step: string; pct: number; detail: string; done: boolean; error: string | null } | null
  analysed: boolean
  climate?: { source: string; years: number; elevation: number; annual_rain_mm: number; annual_tmean: number; monthly_rain_prob: number[] }
  soil?: { sand: number; clay: number; om: number; ph: number; source: string; texture: string }
  imagery?: { source: string; zoom: number; gsd_m: number; size_px: number[]; veg_cover_pct: number; preview_corners: number[][]; error?: string
    roads: { lnglat: number[][]; width_m: number; length_m: number; confidence: number; inside: boolean; distance_m: number }[]
    obstacles: { kind: string; lnglat: number[][]; area_m2: number; inside: boolean }[] }
}
export interface SoilLayers { layers: Record<string, { unit: string; min: number; max: number }>; coordinates: number[][]; summary: Record<string, unknown> }

export const SCENARIOS: { kind: string; label: string; icon: string; days: number }[] = [
  { kind: 'drought', label: 'Drought', icon: 'sun', days: 30 },
  { kind: 'heatwave', label: 'Heatwave', icon: 'thermometer', days: 5 },
  { kind: 'wet_spell', label: 'Wet spell / flood', icon: 'cloud-rain', days: 7 },
  { kind: 'frost', label: 'Frost', icon: 'snowflake', days: 2 },
  { kind: 'storm', label: 'Storm & hail', icon: 'zap', days: 1 },
  { kind: 'pest_outbreak', label: 'Pest / disease outbreak', icon: 'bug', days: 1 },
  { kind: 'irrigation_failure', label: 'Irrigation failure', icon: 'droplet-off', days: 6 },
]

export const worldApi = {
  state: () => req<WorldState>('/world/state'),
  weather: (days = 60) => req<{ history: DayWeather[]; outlook: Outlook[] }>(`/world/weather?days=${days}`),
  daily: (days = 180) => req<(Kpi & { date: string })[]>(`/world/daily?days=${days}`),
  forecast: () => req<Forecast & { section_yield_t_ha?: number[] }>('/world/forecast'),
  refreshForecast: () => post<Forecast>('/world/forecast/refresh'),
  anomalies: (activeOnly = false) => req<(Anomaly & { id: string; started: string; ended: string | null; active: boolean })[]>(`/world/anomalies?active_only=${activeOnly}`),
  clock: (body: { day_seconds?: number; paused?: boolean }) => post<Clock>('/world/clock', body),
  addScenario: (body: { kind: string; days: number; intensity: number; start_in_days?: number }) => post<Scenario>('/world/scenarios', body),
  clearScenarios: () => req<{ ok: boolean }>('/world/scenarios', { method: 'DELETE' }),
  irrigation: (body: { enabled?: boolean; mad?: number; capacity_mm?: number }) => post<WorldState['irrigation']>('/world/irrigation', body),
  soilLayers: () => req<SoilLayers>('/world/soil/layers'),
  truth: () => req<Record<string, number | string>[]>('/world/truth'),
  // farm setup
  analyze: (farmId: string) => post<{ started: boolean }>(`/farms/${farmId}/analyze`),
  analysis: (farmId: string) => req<Analysis>(`/farms/${farmId}/analysis`),
  crops: (farmId: string, refresh = false) => req<CropOption[]>(`/farms/${farmId}/crops${refresh ? '?refresh=true' : ''}`),
  setCrop: (farmId: string, body: { crop: string; rotation_crop?: string | null }) => post<{ crop_plan: unknown }>(`/farms/${farmId}/crop`, body),
}

export const STAGE_COLORS: Record<string, string> = {
  fallow: '#6b5638', sown: '#7d6a3e', vegetative: '#3fa45a', flowering: '#9fc24a', grain_fill: '#c9a640', ripe: '#e2b44a', harvested: '#8d7d5c',
  seeded: '#7d6a3e', growing: '#3fa45a',
}
export const STAGE_LABEL: Record<string, string> = {
  fallow: 'Fallow', sown: 'Sown', vegetative: 'Vegetative', flowering: 'Flowering', grain_fill: 'Grain fill', ripe: 'Ripe', harvested: 'Harvested',
}
export const OP_LABEL: Record<string, string> = { seed: 'Sowing', fertilise: 'Side-dress N', weed: 'Weeding', spray: 'Crop protection', harvest: 'Harvest' }
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function fmtDate(iso?: string, withYear = false) {
  if (!iso) return '—'
  const d = new Date(iso + 'T00:00:00')
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', ...(withYear ? { year: 'numeric' } : {}) })
}
