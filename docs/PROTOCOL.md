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
WGS84 lat/lng before fan-out to dashboards. `arcadia_msgs/Event.section` names the bed section an
event concerns (empty if none).

## Events (`kind` → payload)

| kind | emitted by | payload | backend effect |
|---|---|---|---|
| `world_loaded` | supervisor / twin | `{sections, source}` | log |
| `robot_online` | robot controller at boot | `{type, active, battery_pct, x, y}` | fail its in-flight missions, resolve its incidents, reset to idle at the reported position |
| `section_seen` | supervisor / twin (UAV within `sensor_radius_m` + half a section; 20 s cooldown per section) | `{section, distance_m?, altitude_m?}` | the world model turns it into a noisy multispectral measurement (NDVI, CWSI, weeds, disease, yield estimate) stored on `sections` and pushed as `section` |
| `mission_started` / `mission_complete` / `mission_failed` | robot | `{kind, reason?, kg?, section?, sections_observed?}` | mission lifecycle, robot status |
| `seeded` / `fertilised` / `weeded` / `sprayed` / `harvested` | robot (end of the work pass) | `{section, crop}` | the world model applies the operation to the field truth; a harvest books a `yields` row only for crop that was ripe (`kg`, `was` added to the event) |
| `fault_detected` | robot | `{fault, params, x, y, battery_pct, ...}` | creates an `incidents` row (ERROR → requires tow for `motor_jam`/`battery_drop`), interruptive modal |
| `fault_cleared` | robot | `{recharged}` | incident resolved, robot idle/charging |
| `tow_attached` / `tow_complete` | AGV | `{target}` | towed status; on complete the backend issues `clear_fault{recharge:true}` |
| `charging_started` / `charged` | robot | `{battery_pct?}` | status |
| `robot_power` | robot | `{active}` | fleet active flag (AGV wake/sleep) |

## Commands (`kind` → payload)

| kind | payload | consumer |
|---|---|---|
| `world_load` | `{farm_id, world}` (world config schema 4: `sections`, `tracks`, `track_graph`, `hazards`, `infrastructure`, `robots`) | supervisor spawns/respawns the farm; robots learn their docks and the track graph |
| `mission` | `{mission_id, kind, waypoints:[{x,y,z,lat?,lng?}], section?, bed?, crop?, pass_from?, loop?}` | robot (look-ahead path follower). Ground units first join the tracks from their real pose; waypoints after `pass_from` are the work pass at 0.5 m/s (harvest: down the bed's centre line) |
| `tow` | `{mission_id, target_robot, pickup_index, waypoints}` | AGV; on-track route to the unit (`pickup_index`), then to the repair station |
| `cancel`, `return_home` | `{mission_id?}` | robot |
| `chaos` | `{fault, params, source}` | robot (motor jam locks a wheel, battery drop sets SoC, latency delays odometry, glitch biases localisation) |
| `clear_fault` | `{recharge}` | robot; supervisor re-parks it at the dock when recharging |
| `set_active` | `{active}` | robot (AGV power up/down) and supervisor (park) |
| `set_irradiance` | `{irradiance, sim_hour}` | robots (passive solar skins) — broadcast by the supervisor every 5 s |
| `field_state` | `{s: {section: [height_m, r, g, b, weeds]}}` (deltas, every 2 s) | supervisor paints the world model's crop |
| `weather` | `{sun_elev, sun_az, light, sky, luminosity, fog_m, wet_tracks, hour}` (every 5 s) | supervisor sun/sky/haze; twin + supervisor bog vehicles down only on wet tracks |
| `set_irradiance` (broadcast) | `{irradiance, sim_hour}` | robots' solar skins |
| `pause` / `resume` / `set_speed` | `{factor?}` | headless twin only |

## Backend fan-out (`/ws/telemetry`)

One WebSocket multiplexes `telemetry` frames (≤ 5 Hz/robot) and dashboard messages:
`snapshot`, `fleet`, `fleet_reset`, `bridge`, `section`, `event`, `mission`, `incident`,
`incident_resolved`, `chaos`, `chaos_config`, `dispatch`, `diagnostics`, `energy`.
Telemetry frames are written into a render-free registry and drawn by the Altara canvas at
60 fps with interpolation between the last two samples; only coarse state touches React.
