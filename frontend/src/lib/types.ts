export type RobotStatus = 'idle' | 'active' | 'charging' | 'fault' | 'stranded' | 'towing' | 'towed' | 'offline' | 'returning'

export interface Robot {
  id: string
  type: string
  name: string
  status: RobotStatus | string
  active: boolean
  battery_pct: number
  lat: number | null
  lng: number | null
  x: number
  y: number
  z: number
  heading: number
  speed?: number
  home?: string
  home_dock?: string | null
  current_mission_id?: string | null
  mission_id?: string | null
  fault?: Record<string, unknown> | null
  diagnostics?: { level: string; items: Record<string, { level: string; message: string; values?: Record<string, string> }>; message: string } | null
  latency_ms?: number
  manual_override?: boolean
  last_seen?: number | string | null
  spec?: Record<string, unknown>
}

export interface Farm {
  id: string
  name: string
  boundary: GeoJSON.Feature | GeoJSON.Polygon | GeoJSON.FeatureCollection
  centroid_lat: number
  centroid_lng: number
  area_m2: number
  status: 'mapped' | 'planned' | 'active' | 'archived'
  plan: PlanReport | null
  crop_plan?: { crop?: string; rotation_crop?: string | null; sections?: Record<string, string>; start_date?: string } | null
  seed: number
  created_at: string
}

export interface PlannedSite {
  slug: string; kind: string; x: number; y: number; lat: number; lng: number
  cluster_size: number; capacity_kw: number; storage_kwh: number
}

export interface Bed { id: string; crop: string; sections: number; length_m: number }

export interface LayoutSummary {
  angle_deg: number; params: PlanParams; beds: Bed[]; sections: number; tramlines: number; alleys: number; hazards: number
  field_area_m2: number; crop_area_m2: number; track_length_m: number; cropped_pct: number
}

export interface PlanReport {
  docks: PlannedSite[]; storage: PlannedSite[]; repair: PlannedSite[]; layout?: LayoutSummary
  k_docks: number; k_storage: number; inertia: number; iterations: number; converged: boolean
  mean_travel_m: number; p95_travel_m: number; max_travel_m: number
}

export interface PlanParams {
  k_docks?: number | null; k_storage?: number | null; seed?: number | null
  bed_width: number; lane_width: number; headland_width: number; section_length: number; alley_spacing: number
  orientation: 'auto' | 'ew' | 'ns'; shuffle_crops: boolean
}
export interface ChaosPreset { id: string; label: string; robot_type: string; kind: string }

export interface Infrastructure {
  id: string; slug: string; kind: string; lat: number; lng: number; x: number; y: number
  cluster_size: number; capacity_kw: number; storage_kwh: number; stored_kwh: number
}

export interface Mission {
  id: string; farm_id: string | null; robot_id: string | null; kind: string; status: string; priority: number
  source: string; target_section: string | null; waypoints: Waypoint[] | null; payload: Record<string, unknown> | null
  result: Record<string, unknown> | null; created_at: string; dispatched_at: string | null; completed_at: string | null
  note: string | null
}

export interface Waypoint { x: number; y: number; z?: number; lat?: number; lng?: number }

export interface Incident {
  id: string; farm_id: string | null; robot_id: string; kind: string; severity: string; state: string; title: string
  detail: Record<string, unknown> | null; recovery_mission_id: string | null; requires_tow: boolean
  created_at: string; acknowledged_at: string | null; resolved_at: string | null
}

export interface TelemetryFrame {
  t: 'telemetry'; robot_id: string; type?: string; status: string; x: number; y: number; z: number; heading: number
  speed: number; battery_pct: number; battery_v: number; motor_temp_c: number; cpu_load: number
  mission_id?: string | null; lat?: number; lng?: number; stamp?: number; latency_ms?: number
}

export interface DockReading {
  slug: string; kind: string; generated_kw: number; consumed_kw: number; stored_kwh: number; storage_kwh: number
  irradiance: number; wind_ms: number; sim_hour: number; soc: number; t: number
}

export interface BridgeStatus { mode: string; ros_connected: boolean; farm_id?: string | null; telemetry?: number; events?: number }

export interface ChaosConfig { enabled: boolean; interval_s: number; probability: number; kinds: string[] }

export interface SystemStatus {
  db: boolean; redis: boolean; celery_workers: string[]; bridge: BridgeStatus & { rosbridge_url: string }
  dispatch_paused: boolean; chaos: ChaosConfig; active_farm: string | null
}

export interface FeedEvent { id: number; t: number; kind: string; robot_id?: string | null; section?: string | null; severity: string; payload: Record<string, unknown> }

export const ROBOT_LABEL: Record<string, string> = {
  scout_uav: 'Scout UAV', seeder_ugv: 'Seeder UGV', cropcare_ugv: 'Crop Care UGV', harvester_ugv: 'Harvester UGV', logistics_agv: 'Logistics AGV',
}
export const ROBOT_COLOR: Record<string, string> = {
  scout_uav: '#5cc8ff', seeder_ugv: '#f5c451', cropcare_ugv: '#7ce38b', harvester_ugv: '#ff9f6b', logistics_agv: '#c7a4ff',
}
export const INFRA_COLOR: Record<string, string> = { solar_dock: '#f5c451', wind_dock: '#5cc8ff', storage: '#c7a4ff', repair_station: '#ff9f6b', road: '#9aa3a7' }
export const TRACK_COLOR: Record<string, string> = { headland: '#b39b78', alley: '#b39b78', spur: '#a7a39a', lane: '#8f7a5c' }
export const TRACK_LABEL: Record<string, string> = { headland: 'Headland (turning track)', alley: 'Farm track / cross alley', spur: 'Yard spur', lane: 'Tramline (wheel lane)' }
export const INFRA_LABEL: Record<string, string> = { solar_dock: 'Solar charging dock', wind_dock: 'Wind charging dock', storage: 'Robot storage', repair_station: 'Repair station', road: 'Travel road' }
export const CROP_COLORS: Record<string, string> = { wheat: '#e2c27a', maize: '#f0a742', soybean: '#8fd17a', soy: '#8fd17a', tomato: '#ff6b5e', lettuce: '#5fd7a0' }

export const STATUS_COLOR: Record<string, string> = {
  idle: '#a3b8aa', active: '#7ce38b', returning: '#5cc8ff', charging: '#f5c451', fault: '#ff9f6b', stranded: '#ff4d5e',
  towing: '#c7a4ff', towed: '#ff7a59', offline: '#4b5f52',
}
