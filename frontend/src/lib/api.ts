import type { ChaosConfig, ChaosPreset, Farm, Incident, Infrastructure, Mission, PlanParams, Robot, SystemStatus } from './types'

const BASE = '/api'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!res.ok) {
    let detail: unknown = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* ignore */ }
    // FastAPI validation errors arrive as a list of {loc, msg}; show them as one readable line
    if (Array.isArray(detail)) detail = detail.slice(0, 3).map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}`).join(' · ')
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}
const post = <T,>(path: string, body?: unknown) => req<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const put = <T,>(path: string, body?: unknown) => req<T>(path, { method: 'PUT', body: JSON.stringify(body) })

export const api = {
  farms: () => req<Farm[]>('/farms'),
  farm: (id: string) => req<Farm>(`/farms/${id}`),
  createFarm: (body: { name: string; boundary: unknown; resolution?: number | null; target_cells?: number | null; seed?: number }) => post<Farm>('/farms', body),
  deleteFarm: (id: string) => req<void>(`/farms/${id}`, { method: 'DELETE' }),
  hexgrid: (id: string) => req<GeoJSON.FeatureCollection>(`/farms/${id}/hexgrid`),
  infrastructure: (id: string) => req<Infrastructure[]>(`/farms/${id}/infrastructure`),
  plan: (id: string, body: Partial<PlanParams>) => post<Farm>(`/farms/${id}/plan`, body),
  resetAll: () => req<void>('/farms', { method: 'DELETE' }),
  activate: (id: string) => post<Farm>(`/farms/${id}/activate`),
  farmStats: (id: string) => req<{ states: Record<string, number>; weeds: number; surveyed: number; avg_moisture: number }>(`/farms/${id}/stats`),
  world: (id: string) => req<unknown>(`/farms/${id}/world`),

  fleet: () => req<Robot[]>('/fleet'),
  dispatch: (robotId: string, body: { kind?: string; target_h3?: string; lat?: number; lng?: number; note?: string }) => post<Mission>(`/fleet/${robotId}/dispatch`, body),
  returnHome: (robotId: string) => post<Mission>(`/fleet/${robotId}/return-home`),
  route: (robotId: string, body: { waypoints: { lat: number; lng: number }[]; loop: boolean; kind?: 'patrol' | 'goto' | 'survey'; note?: string }) => post<Mission>(`/fleet/${robotId}/route`, body),
  stop: (robotId: string) => post<{ ok: boolean }>(`/fleet/${robotId}/stop`),
  resumeAuto: (robotId: string) => post<{ ok: boolean }>(`/fleet/${robotId}/resume-auto`),
  power: (robotId: string, active: boolean) => post<{ ok: boolean }>(`/fleet/${robotId}/power?active=${active}`),

  missions: (status?: string, limit = 60) => req<Mission[]>(`/missions?limit=${limit}${status ? `&status=${status}` : ''}`),
  cancelMission: (id: string) => post<Mission>(`/missions/${id}/cancel`),
  queueState: () => req<{ paused: boolean }>('/missions/queue/state'),
  pauseQueue: () => post<{ paused: boolean }>('/missions/queue/pause'),
  resumeQueue: () => post<{ paused: boolean }>('/missions/queue/resume'),

  incidents: (state?: string) => req<Incident[]>(`/incidents${state ? `?state=${state}` : ''}`),
  ackIncident: (id: string) => post<Incident>(`/incidents/${id}/ack`),
  recoverIncident: (id: string) => post<Incident>(`/incidents/${id}/recover`),

  chaosConfig: () => req<ChaosConfig>('/chaos/config'),
  setChaosConfig: (cfg: ChaosConfig) => put<ChaosConfig>('/chaos/config', cfg),
  inject: (body: { robot_id?: string; kind?: string }) => post<{ robot_id: string; fault: string }>('/chaos/inject', body),
  chaosPresets: () => req<ChaosPreset[]>('/chaos/presets'),
  runPreset: (id: string) => post<{ robot_id: string; fault: string }>(`/chaos/presets/${id}`),
  setView: (follow: string | null, mode: 'pan' | 'tracking' = 'pan') => post<{ ok: boolean }>(`/fleet/view?follow=${follow ?? ''}&mode=${mode}`),

  telemetry: (robotId: string, minutes = 10) => req<Array<Record<string, number | string | null>>>(`/telemetry/${robotId}?minutes=${minutes}`),
  yields: () => req<{ by_crop: { crop: string; kg: number; cells: number }[]; total_kg: number; recent: unknown[] }>('/analytics/yields'),
  energy: (minutes = 30) => req<{ live: unknown[]; history: unknown[] }>(`/analytics/energy?minutes=${minutes}`),
  status: () => req<SystemStatus>('/system/status'),
}
