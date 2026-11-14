# ARCADIA wire protocol (simulation ⇄ backend)

Both simulation back-ends — the native Webots/ROS 2 stack and the in-process headless twin —
speak exactly the same protocol, so the orchestrator, Celery tasks and dashboard never know
which one is driving. In ROS 2 the messages live in `arcadia_msgs`; over rosbridge they are
plain JSON with the same field names.

| Direction | ROS topic | Type | Rate |
|---|---|---|---|
| sim → backend | `/arcadia/telemetry` | `arcadia_msgs/Telemetry` | 5 Hz per robot (throttled from 50 Hz `/arcadia/<robot>/odom`) |
| sim → backend | `/arcadia/event` | `arcadia_msgs/Event` | on change |
| sim → backend | `/diagnostics_agg` | `diagnostic_msgs/DiagnosticArray` | 2 Hz (diagnostic_aggregator roll-up) |
| backend → sim | `/arcadia/command` | `arcadia_msgs/Command` | on demand |
| robot → throttle | `/arcadia/<robot>/state` | `arcadia_msgs/State` | 2 Hz (authoritative status/mission/fault) |

Robot ids use hyphens (`uav-1`); ROS topic namespaces use underscores (`/arcadia/uav_1/...`).
Positions are local ENU metres relative to the farm centroid (`farm.origin`); the backend adds
WGS84 lat/lng and the H3 cell before fan-out to dashboards.

## Events (`kind` → payload)

| kind | emitted by | payload | backend effect |
|---|---|---|---|
| `world_loaded` | supervisor / twin | `{cells, source}` | log |
| `robot_online` | robot controller at boot | `{type, active, battery_pct, x, y}` | fail its in-flight missions, resolve its incidents, reset to idle |
| `cell_observed` | supervisor (UAV within `sensor_radius_m`) | `{state, weeds, soil_moisture, health}` | update `hex_cells`, push `cell` to dashboards (mock multispectral CV) |
| `mission_started` / `mission_complete` / `mission_failed` | robot | `{kind, reason?, kg?, cells_observed?}` | mission lifecycle, robot status |
| `seeded` / `weeded` / `harvested` | robot (after tool dwell) | `{crop, kg?}` | cell state, `yields` row; the supervisor mutates the crop ground truth |
| `fault_detected` | robot | `{fault, params, x, y, battery_pct, ...}` | creates an `incidents` row (ERROR → requires tow for `motor_jam`/`battery_drop`), interruptive modal |
| `fault_cleared` | robot | `{recharged}` | incident resolved, robot idle/charging |
| `tow_attached` / `tow_complete` | AGV | `{target}` | towed status; on complete the backend issues `clear_fault{recharge:true}` |
| `charging_started` / `charged` | robot | `{battery_pct?}` | status |
| `robot_power` | robot | `{active}` | fleet active flag (AGV wake/sleep) |

## Commands (`kind` → payload)

| kind | payload | consumer |
|---|---|---|
| `world_load` | `{farm_id, world}` (full world config, schema 1) | supervisor spawns/respawns the farm; robots learn their home dock |
| `mission` | `{mission_id, kind, waypoints:[{x,y,z,h3?}], target_h3?, crop?}` | robot (pure-pursuit waypoint follower) |
| `tow` | `{mission_id, target_robot, waypoints}` | AGV; second waypoint is the drop-off dock |
| `cancel`, `return_home` | `{mission_id?}` | robot |
| `chaos` | `{fault, params, source}` | robot (motor jam locks a wheel, battery drop sets SoC, latency delays odometry, glitch biases localisation) |
| `clear_fault` | `{recharge}` | robot; supervisor re-parks it at the dock when recharging |
| `set_active` | `{active}` | robot (AGV power up/down) and supervisor (park) |
| `set_irradiance` | `{irradiance, sim_hour}` | robots (passive solar skins) — broadcast by the supervisor every 5 s |
| `pause` / `resume` / `set_speed` | `{factor?}` | headless twin only |

## Backend fan-out (`/ws/telemetry`)

One WebSocket multiplexes `telemetry` frames (≤ 5 Hz/robot) and dashboard messages:
`snapshot`, `fleet`, `fleet_reset`, `bridge`, `cell`, `event`, `mission`, `incident`,
`incident_resolved`, `chaos`, `chaos_config`, `dispatch`, `diagnostics`, `energy`.
Telemetry frames are written into a render-free registry and drawn by the Altara canvas at
60 fps with interpolation between the last two samples; only coarse state touches React.
