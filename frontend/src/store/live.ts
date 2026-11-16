/**
 * Live state store. One WebSocket (/ws/telemetry) multiplexes throttled telemetry frames and
 * dashboard events. Telemetry is written into a mutable, render-free registry consumed by the
 * Altara canvas renderer at 60fps; only coarse state (status, battery) touches React.
 */
import { create } from 'zustand'
import { useWorld } from './world'
import type { BridgeStatus, DockReading, FeedEvent, Incident, Mission, Robot, TelemetryFrame } from '../lib/types'

export interface TrackPoint { x: number; y: number; lat: number; lng: number; t: number }
export interface LiveRobot {
  id: string
  type: string
  // interpolation targets
  lat: number; lng: number; z: number; heading: number; speed: number
  prevLat: number; prevLng: number; prevHeading: number
  tPrev: number; tNext: number
  battery: number; status: string; latency: number
  trail: TrackPoint[]
}

/** Render-free registry (not in React state) */
export const liveRegistry = new Map<string, LiveRobot>()
/** Live bed-section state pushed by the backend ('section' messages), keyed by section id. */
export const sectionOverrides = new Map<string, Record<string, unknown>>()
let cellVersion = 0

function ingestTelemetry(f: TelemetryFrame) {
  if (f.lat == null || f.lng == null) return
  const now = performance.now()
  let r = liveRegistry.get(f.robot_id)
  if (!r) {
    r = { id: f.robot_id, type: f.type ?? 'ugv', lat: f.lat, lng: f.lng, z: f.z, heading: f.heading, speed: f.speed,
      prevLat: f.lat, prevLng: f.lng, prevHeading: f.heading, tPrev: now, tNext: now + 200, battery: f.battery_pct,
      status: f.status, latency: f.latency_ms ?? 0, trail: [] }
    liveRegistry.set(f.robot_id, r)
  } else {
    r.prevLat = r.lat; r.prevLng = r.lng; r.prevHeading = r.heading
    r.tPrev = now; r.tNext = now + Math.min(400, Math.max(120, now - (r.tPrev || now)))
    r.lat = f.lat; r.lng = f.lng; r.z = f.z; r.heading = f.heading; r.speed = f.speed
    r.battery = f.battery_pct; r.status = f.status; r.latency = f.latency_ms ?? 0
    if (f.type) r.type = f.type
  }
  const last = r.trail[r.trail.length - 1]
  if (!last || Math.hypot(last.x - f.x, last.y - f.y) > 0.5) {
    r.trail.push({ x: f.x, y: f.y, lat: f.lat, lng: f.lng, t: now })
    if (r.trail.length > 400) r.trail.shift()
  }
}

interface LiveState {
  connected: boolean
  bridge: BridgeStatus | null
  fleet: Record<string, Robot>
  docks: Record<string, DockReading>
  energyHist: Record<string, number[]>
  incidents: Incident[]
  pendingModal: Incident | null
  missions: Mission[]
  feed: FeedEvent[]
  dispatchPaused: boolean
  chaosFlash: { robot_id: string; fault: string; t: number } | null
  cellVersion: number
  telemetryRate: number
  setFleet: (robots: Robot[]) => void
  setIncidents: (i: Incident[]) => void
  setMissions: (m: Mission[]) => void
  dismissModal: () => void
  connect: () => () => void
}

let feedId = 0
let rateCount = 0

export const useLive = create<LiveState>((set, get) => ({
  connected: false, bridge: null, fleet: {}, docks: {}, energyHist: {}, incidents: [], pendingModal: null, missions: [], feed: [],
  dispatchPaused: false, chaosFlash: null, cellVersion: 0, telemetryRate: 0,
  setFleet: (robots) => set({ fleet: Object.fromEntries(robots.map((r) => [r.id, { ...get().fleet[r.id], ...r }])) }),
  setIncidents: (incidents) => set({ incidents }),
  setMissions: (missions) => set({ missions }),
  dismissModal: () => set({ pendingModal: null }),
  connect: () => {
    let ws: WebSocket | null = null
    let closed = false
    let retry = 1000
    const rateTimer = setInterval(() => { set({ telemetryRate: rateCount }); rateCount = 0 }, 1000)

    const pushFeed = (e: Omit<FeedEvent, 'id' | 't'>) =>
      set((s) => ({ feed: [{ id: ++feedId, t: Date.now(), ...e }, ...s.feed].slice(0, 120) }))

    const open = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/telemetry`)
      ws.onopen = () => { set({ connected: true }); retry = 1000 }
      ws.onclose = () => { set({ connected: false }); if (!closed) setTimeout(open, (retry = Math.min(retry * 1.6, 10000))) }
      ws.onerror = () => ws?.close()
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data)
        switch (m.t) {
          case 'telemetry': rateCount++; ingestTelemetry(m as TelemetryFrame); break
          case 'snapshot': {
            const fleet = m.fleet as Record<string, Robot>
            set({ fleet, bridge: m.bridge, dispatchPaused: !!m.dispatch_paused,
              docks: Object.fromEntries((m.energy as DockReading[]).map((d) => [d.slug, d])) })
            break
          }
          case 'fleet': set((s) => ({ fleet: { ...s.fleet, [m.robot.id]: { ...s.fleet[m.robot.id], ...m.robot } } })); break
          case 'fleet_reset': {
            liveRegistry.clear(); sectionOverrides.clear()
            set({ fleet: Object.fromEntries((m.robots as Robot[]).map((r) => [r.id, r])), feed: [], cellVersion: ++cellVersion })
            break
          }
          case 'bridge': set({ bridge: m }); break
          case 'energy': set((s) => {
            const energyHist = { ...s.energyHist }
            for (const d of m.docks as DockReading[]) energyHist[d.slug] = [...(energyHist[d.slug] ?? []), d.generated_kw].slice(-60)
            return { docks: Object.fromEntries((m.docks as DockReading[]).map((d) => [d.slug, d])), energyHist }
          }); break
          case 'dispatch': set({ dispatchPaused: !!m.paused }); break
          case 'diagnostics': set((s) => {
            const fleet = { ...s.fleet }
            for (const [rid, diag] of Object.entries(m.robots as Record<string, Robot['diagnostics']>)) if (fleet[rid]) fleet[rid] = { ...fleet[rid], diagnostics: diag }
            return { fleet }
          }); break
          case 'section': {
            const { t: _t, id, ...rest } = m
            sectionOverrides.set(id, { ...(sectionOverrides.get(id) ?? {}), ...rest })
            cellVersion++
            set({ cellVersion })
            if (m.op === 'harvest' && m.kg) pushFeed({ kind: 'harvested', robot_id: m.by ?? null, section: id, severity: 'INFO', payload: { kg: m.kg, was: m.was } })
            break
          }
          case 'sections': {
            for (const it of m.items as Record<string, unknown>[]) {
              const { t: _t, id, ...rest } = it
              sectionOverrides.set(id as string, { ...(sectionOverrides.get(id as string) ?? {}), ...rest })
            }
            cellVersion++
            set({ cellVersion })
            useWorld.getState().apply(m)
            break
          }
          case 'sections_sync': { // the world switched timelines: every section's state and observation is replaced
            sectionOverrides.clear()
            for (const it of m.items as Record<string, unknown>[]) {
              const { id, ...rest } = it
              sectionOverrides.set(id as string, rest)
            }
            cellVersion++
            set({ cellVersion })
            break
          }
          case 'world': case 'world_day': case 'world_mode': case 'forecast': case 'analysis': useWorld.getState().apply(m); break
          case 'anomaly': pushFeed({ kind: 'anomaly', robot_id: null, severity: m.anomaly.severity === 'critical' ? 'ERROR' : 'WARN', payload: { title: m.anomaly.title } }); break
          case 'event': {
            const e = m.event
            if (e.kind !== 'section_observed' && e.kind !== 'section_seen') pushFeed({ kind: e.kind, robot_id: e.robot_id, section: e.section, severity: e.severity, payload: e.payload ?? {} })
            break
          }
          case 'mission': set((s) => {
            const idx = s.missions.findIndex((x) => x.id === m.id)
            const missions = [...s.missions]
            if (idx >= 0) missions[idx] = { ...missions[idx], status: m.status, result: m.result ?? missions[idx].result }
            else missions.unshift({ id: m.id, status: m.status, robot_id: m.robot_id, kind: m.kind ?? '?', farm_id: null, priority: 5,
              source: 'auto', target_section: null, waypoints: null, payload: null, result: null, created_at: new Date().toISOString(),
              dispatched_at: null, completed_at: null, note: null })
            return { missions: missions.slice(0, 80) }
          }); break
          case 'incident': {
            set((s) => {
              const idx = s.incidents.findIndex((x) => x.id === m.id)
              const incidents = [...s.incidents]
              const inc: Incident = idx >= 0 ? { ...incidents[idx], ...m } : { id: m.id, farm_id: null, robot_id: m.robot_id, kind: m.kind,
                severity: m.requires_tow ? 'ERROR' : 'WARN', state: m.state ?? 'open', title: m.title ?? m.kind, detail: m.detail ?? null,
                recovery_mission_id: m.recovery_mission_id ?? null, requires_tow: !!m.requires_tow, created_at: new Date().toISOString(),
                acknowledged_at: null, resolved_at: null }
              if (idx >= 0) incidents[idx] = inc; else incidents.unshift(inc)
              const pendingModal = idx < 0 && inc.state === 'open' ? inc : s.pendingModal
              return { incidents, pendingModal }
            })
            if (m.state === 'open' || !m.state) pushFeed({ kind: 'incident', robot_id: m.robot_id, severity: 'ERROR', payload: { title: m.title, kind: m.kind } })
            break
          }
          case 'incident_resolved': set((s) => ({ incidents: s.incidents.map((i) => i.robot_id === m.robot_id && i.state !== 'resolved' ? { ...i, state: 'resolved', resolved_at: new Date().toISOString() } : i) })); break
          case 'chaos': set({ chaosFlash: { robot_id: m.robot_id, fault: m.fault, t: Date.now() } }); pushFeed({ kind: 'chaos_injected', robot_id: m.robot_id, severity: 'WARN', payload: { fault: m.fault, ...m.params } }); break
          default: break
        }
      }
    }
    open()
    const ping = setInterval(() => ws?.readyState === 1 && ws.send('{"t":"ping"}'), 15000)
    return () => { closed = true; clearInterval(ping); clearInterval(rateTimer); ws?.close() }
  },
}))
