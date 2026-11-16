# Project ARCADIA — Autonomous Agricultural Digital Twin & Robotics Ecosystem

End-to-end implementation of the ARCADIA engineering spec, tuned for a Lenovo IdeaPad Slim 3
(i5-13420H, 16 GB, Intel iGPU, Ubuntu 26.04): the **web stack is containerised**, the
**robotics stack (ROS 2 Lyrical + Webots R2025a) runs natively**, the sim uses **primitive
geometry only**, computer vision is **mocked with proximity events in the Webots Supervisor**,
and the local fleet is **capped at 2 active robots** (UAV + UGV; the Logistics AGV boots
powered-down and is only woken for tow recoveries).

```
ARCADIA/
├── backend/        FastAPI orchestrator · Celery dispatch/chaos/recovery/energy · ROSbridge adapter · headless twin
├── frontend/       React 19 + Vite + MapLibre + "Altara" 60 fps canvas swarm renderer
├── robotics/       Native ROS 2 workspace: arcadia_msgs, arcadia_sim (Webots controllers), arcadia_supervisor (C++), arcadia_bringup
├── infra/caddy/    Reverse proxy (SPA, /api, /ws, /rosbridge, /webots-stream) with internal TLS on :8443
├── docker-compose.yml   caddy · api · worker · beat · web · postgres (TimescaleDB) · redis
└── scripts/        dev_backend.sh (native backend), seed_demo.py (demo farm)
```

## Quick start

One line (Docker is installed if missing; free ports are picked; a random database password is written to `.env`):

```bash
git clone https://github.com/SHINO-01/ARCADIA.git && ARCADIA/setup.sh
```

| Option | Effect |
|---|---|
| `--full` | also install ROS 2 Lyrical and Webots R2025a natively and build the workspace (Ubuntu x86-64), then `make sim` |
| `--tunnel` | public https URL through a free Cloudflare quick tunnel (`--tunnel-token TOKEN` for your own domain) |
| `--cloud` | server mode: headless digital twin, databases on localhost only |
| `--demo` | create, analyse, lay out, plant and activate a demo farm |
| `--port N`, `--project NAME` | dashboard port; compose project name for a second install on one machine |

Once the repository is public: `curl -fsSL https://raw.githubusercontent.com/SHINO-01/ARCADIA/main/setup.sh | bash -s -- --tunnel`.
Free hosting options are compared in [docs/HOSTING.md](docs/HOSTING.md).

Manual steps, if you prefer:

```bash
sudo bash robotics/scripts/install_ros2_apt.sh            # ROS 2 Lyrical (once)
cp .env.example .env && make up                           # web stack: http://localhost:8080
bash robotics/scripts/install_webots_tarball.sh           # Webots R2025a -> ~/opt/webots
make robotics-build && make sim                           # Webots + fleet + rosbridge :9090
make seed-demo                                            # or set up a farm in the dashboard
```

Day to day, `docker compose up -d` starts the whole project and `docker compose down` stops it;
`make up` does the same but rebuilds the images first, which is needed after pulling new code.
The web stack (dashboard at http://localhost:8080) runs in Docker. Webots and ROS 2 stay native
for performance; the **sim agent** (`make sim-agent-install`, a systemd user service that
`setup.sh --full` installs) starts `make sim` as soon as the stack is healthy and stops it once
the stack has been down for 45 s, so the one Docker command drives the simulation too. Set
`ARCADIA_SIM_GUI=false` in `.env` for a minimised Webots; `make sim-agent-logs` follows it.

Without `make sim` the backend's **headless digital twin** drives the whole platform (same wire
protocol), and the bridge hot-swaps to ROS 2 as soon as rosbridge on :9090 becomes reachable.

### Native backend for development
```bash
docker compose up -d postgres redis
scripts/dev_backend.sh start      # API :8000 + Celery worker + beat; logs in .run/
cd frontend && npm run dev        # Vite :5173 proxies /api and /ws to :8000
```

## Mission-control UI

The dashboard is grouped into six views (left rail, keys `1`–`6`). The UI follows the design system in `design-system/arcadia/MASTER.md` (Swiss/minimal, earth green + harvest gold on a dark field palette, Fira Sans + Fira Code, visible focus states, reduced-motion support).

| View | What it is for |
|---|---|
| **Farm** | First run opens a welcome dialog: find the land (OpenStreetMap place search), then draw the field. A five-step wizard follows: boundary → **site analysis** (satellite road/obstacle detection, ERA5 climate, SoilGrids soil, with live progress) → **adaptive layout** → **crop** (Monte Carlo season simulation of every crop on this farm) → go live. |
| **World** | The agronomic world model on two separate timelines: the **real-time twin** (default; farm time runs 1:1 with the wall clock, in local solar time, and catches up after downtime) and the **simulation sandbox** (forked from the live state, with its own history; fast-forward, pause, restart and what-if scenarios). Live weather and a 7-day probabilistic outlook, crop stages, canopy/NDVI/biomass/soil N, root-zone water and irrigation, yield forecast (P10/P50/P90), field-operation campaigns, statistical anomalies and the scenario lab (sandbox only: drought, heatwave, wet spell, frost, storm, pest outbreak, irrigation failure). |
| **Operations** | Live map + fleet roster + RTS command bar (sow, fertilise, weed, spray, harvest, move, patrol, dock). Map layers: stage, NDVI, water, health, yield estimate, crop; overlays: detected roads/obstacles, the image classification, soil maps. |
| **3D Twin** | Embedded Webots W3D stream; crop height and colour, weeds, the sun, sky and haze follow the world model. |
| **Alerts** | Agronomic anomaly history, robot incidents, Chaos Engine and the event stream. |
| **Analytics** | Harvest, canopy and biomass, the cumulative water balance (rain, irrigation, ET0), microgrids, fleet telemetry, missions. |

## Agronomic world model (`backend/app/agro`)

A daily, spatially explicit simulation of the field, vectorised over bed sections, and the single source of truth for both simulators (Webots renders it; the headless twin and the robots act on it):

| Component | Method |
|---|---|
| Climate | 10 years of ERA5 daily weather (Open-Meteo) at the field -> Richardson-type generator: monthly Markov rain occurrence, gamma amounts, harmonic means for wet/dry days, multivariate AR(1) residuals; SPI and z-score climatologies |
| Weather | stochastic days + FAO-56 Penman-Monteith ET0, hourly temperature and irradiance profiles, scenario layer |
| Soil & terrain | SoilGrids topsoil means + Gaussian-random-field variability; Saxton & Rawls (2006) hydraulics; D8 terrain wetness index; SCS curve numbers; salinity patches; compaction only on tracks |
| Water | FAO-56 root-zone depletion balance: runoff, run-on to hollows, ponding, waterlogging, Ks stress, automatic irrigation (management-allowed depletion, pump capacity) |
| Crop | thermal-time phenology, logistic LAI, Beer's law interception, RUE biomass, harvest index with heat/frost/drought penalties at flowering, critical-N dilution, Cousens weed competition, Maas-Hoffman salinity, post-maturity losses |
| Weeds & disease | rain-triggered weed emergence; infection on warm wet days spreading to neighbouring sections; sprays protect for 14 days |
| Sensing | UAV sightings (Webots proximity mock CV) become noisy NDVI/CWSI/weed/disease/yield measurements; soil-moisture probes |
| Statistics | anomaly detection (z-scores, SPI-30, CUSUM, robust spatial NDVI outliers with cause inference); Monte Carlo season and crop forecasts |
| Operations | sow / side-dress N / weed / spray / harvest campaigns triggered by agronomy and workable weather, worked by an auto-sized virtual fleet plus the two physical robots |

Crops: one per field (optionally a two-crop rotation block): maize, winter wheat, soybean and processing tomato, with FAO-56/FAO-33 parameters. Calibrated on Fresno's ERA5 climate, managed irrigated yields come out at about 111 t/ha (tomato), 7 t/ha (wheat), 8.7 t/ha (maize) and 2.4 t/ha (soybean).

## Adaptive layout from imagery (`backend/app/services/imagery.py`, `layout.py`)

* **Digital image processing** (numpy + scipy only): satellite tiles at ~0.5 m/px; paved/dirt surface likelihood x multi-scale Hessian ridge filter; hysteresis threshold and morphology; elongation test; Zhang-Suen thinning, m-adjacency skeleton tracing and Douglas-Peucker; widths from the distance transform. Buildings (bright compact rectangles), water and tree cover are classified too.
* **Optimisation**: cropable area = field minus headland, road corridors and obstacles. Each zone gets its own bed direction and offset, searched coarse-to-fine to maximise soil-weighted bed area minus a turning loss per bed end. Concave zones are tried split along reflex-vertex cuts (kept only if better by over 1 %). Existing roads become farm tracks; storage sits next to a road with a gate spur.

**RTS unit control (Operations):** click a unit to select · ctrl/shift+click to add to the selection · right-click the map to move the selection · shift+right-click to add route waypoints, then **Patrol** (looping) or **Go** · `S` stop · `H` nearest dock · `Esc` clear. Operator orders put a unit under manual control; **Auto** hands it back to the dispatcher. Ground routes follow the tracks (the route builder previews the real on-track path before you send it); a UAV flies the clicks directly at 6 m and keeps surveying the sections it overflies.

**Controlled-traffic field layout:** the field is laid out the way mechanised farms protect their soil and crop. Long **beds** run along the field's long axis and are split into ~40 m **sections**, the unit of crop state, observation and work. Permanent dirt **tramlines**, just wide enough for the vehicles, run between the beds. A **headland** track circles the field inside the boundary for turning, and **cross alleys** break long beds. Charging docks (K-Means++ over the bed sections, solar/wind), robot storage and a **repair station** sit on **yard pads** outside the field, each joined to the headland by a short spur. Bed width, tramline width, headland width, section length, alley spacing, bed orientation, dock count and crop rotation are adjustable in metres; re-planning a live farm respawns it in Webots.

**Traffic rules:** ground vehicles only ever drive on the track network (headland, tramlines, alleys, spurs), routed by Dijkstra over the noded tracks. A goal clicked inside a bed snaps to the nearest track. Seeding and weeding are done from the tramline beside the section, with the tool reaching into the bed at 0.5 m/s. **Harvest is the one exception:** the harvester leaves the tramline at the section's end, drives down the bed's centre line cutting the crop, and steps back onto the same tramline. Every controller carries the track graph (`world.track_graph`), so its own moves (low-battery return, return-home, and the approach to a mission planned from a stale position) also stay on the tracks. Controllers follow each track leg with a look-ahead path follower, so they hold the tramline centre line. Weeding only targets planted sections, because seeding cultivates the ground and clears weeds. Harvesting a section that is not ripe yields nothing.

**Stranding scenarios:** motor jam, battery collapse and **terrain entrapment** (waterlogged beds are drawn as hazards on the map and in Webots; ground vehicles crossing them can bog down, and the supervisor pins the chassis until it is winched). Tows drive the tracks to the unit (shortest path from where the AGV actually is: parked units keep reporting their position), winch it and deliver it to the **repair station**. A unit that keeps a mission but stops moving for 90 s raises a `mission_stalled` warning in the event feed. Low-battery units head for the **nearest** charging dock, decided by route length and remaining range.

## Layer map (spec → implementation)

| Spec | Where |
|---|---|
| Geo-selection → GeoJSON | `frontend/src/components/FarmMap.tsx` (MapLibre, built-in boundary tool) |
| Field geometry (local ENU plane) | `backend/app/services/field.py`, `geo.py` |
| Controlled-traffic layout | `backend/app/services/layout.py` (beds, sections, tramlines, headland, alleys) |
| Track network router | `backend/app/services/track_network.py` (noded tracks, near-miss healing, Dijkstra); on-board copy in `arcadia_sim/behaviour.py` (`TrackGraph`) |
| K-Means dock/storage planning | `backend/app/services/planner.py` (numpy k-means++, travel-latency metrics) |
| World-config JSON payload | `backend/app/services/world_builder.py` → `GET /api/farms/{id}/world` |
| C++ Webots Supervisor spawner | `robotics/ros2_ws/src/arcadia_supervisor/` (terrain, bed sections, tracks, docks, storage, robots, ground truth, tow mechanics) |
| Robot models + controllers | `arcadia_sim/protos/*.proto`, `arcadia_sim/arcadia_sim/robot_controller.py` (pure-pursuit + LiDAR avoidance, torque-coupled battery) |
| Mock CV (ripeness/weeds) | Supervisor proximity events `section_observed` (UAV sensor radius) |
| ROSbridge translation | `arcadia_bringup` launches `rosbridge_websocket`; backend client `backend/app/bridge/rosbridge_client.py` |
| Telemetry throttling | `arcadia_sim/arcadia_sim/telemetry_throttle.py` (50 Hz odom → 5 Hz `/arcadia/telemetry`) |
| diagnostic_aggregator | `arcadia_sim/config/diagnostics.yaml` → `/diagnostics_agg` → fleet OK/WARN/ERROR |
| FastAPI + WebSocket ingestion | `backend/app/main.py`, `backend/app/api/telemetry.py` (`/ws/telemetry`) |
| Celery + Redis dispatch | `backend/app/workers/tasks.py` (`auto_dispatch_tick`, `dispatch_mission`) |
| Chaos Engine | `backend/app/services/chaos.py` + `chaos_tick`; faults honoured by both sims |
| Time-series persistence | TimescaleDB hypertables `telemetry`, `energy_log` (`backend/alembic/versions/0001_initial.py`) |
| 60 fps canvas map ("Altara") | `frontend/src/altara/renderer.ts` |
| Split-screen Webots stream | `frontend/src/components/WebotsView.tsx` + `public/webots-viewer.html` (W3D stream on :1234) |
| Click-to-dispatch override | Fleet panel → arm → click a bed section → `POST /api/fleet/{id}/dispatch` (pauses auto for that robot) |
| Maintenance modal + AGV tow | `IncidentModal` → `POST /api/incidents/{id}/recover` → Celery `recovery_protocol` |
| Caddy HTTPS/WebSocket routing | `infra/caddy/Caddyfile` |

## Verified end to end (2026-11-14, IdeaPad Slim 3)
- 2026-11-16 (world model): North Field analysed (116 road segments, 186 obstacles from imagery; ERA5 2016-2025 climate), re-planned adaptively (+7 % productive score vs. one orientation), tomato chosen by the Monte Carlo crop simulation; in Webots the fleet sowed the field in the March window, the UAV scouted it and the robots claimed sections from the same campaign queues. 27 backend tests pass (FAO-56 ET0 example, Saxton-Rawls, yields, SPI drought detection, forecast quantiles, adaptive zoning, roads/obstacles, imagery on a synthetic scene).
- 2026-11-16: the H3 hex index was retired (migration `0004`); bed sections are the only spatial unit. `backend/tests/test_twin.py` drives survey, seed, weed, harvest (ripe and early), goto, a looped ground patrol, return-home and a breakdown tow planned from a stale AGV position, and checks every telemetry frame against the beds.
- Headless twin: farm → layout → K-Means plan → activate → automated survey/seed/weed missions, TimescaleDB telemetry, chaos faults, AGV tow recovery. `make backend-test` passes.
- Native stack: Webots R2025a (tarball) + ROS 2 Lyrical — supervisor spawns the farm from the backend world config, three extern controllers attach, 50 Hz odometry throttled to 5 Hz `/arcadia/telemetry`, `diagnostic_aggregator` roll-ups, rosbridge ↔ FastAPI hot-swap (the backend switches from headless twin to ROS automatically), dashboard embeds the W3D stream.
- Energy autonomy in the physics sim: torque-coupled drain (wheel torque × angular velocity), passive solar skins fed by the supervisor's irradiance broadcast, and a 22% reserve reflex — the Scout UAV abandoned a survey at 22%, flew to dock-1 and charged, without the backend's help.
- Chaos → recovery in the physics sim: `motor_jam` injected into the Seeder UGV → `fault_detected` → interruptive modal → Logistics AGV powers up in storage, drives ~70 m (pure-pursuit + LiDAR), winches the UGV (supervisor drags it), parks it on the dock pad, fault cleared + recharge, incident resolved in 76 s, AGV powers back down.

## Design notes / limits
- The bridge runs in **auto** mode: headless twin until rosbridge appears, then ROS 2; if the native stack stops it falls back again. On any controller restart the robot sends `robot_online`, which fails its in-flight missions and closes its incidents so state never drifts; a Celery watchdog also times out silent missions.
- Nav2/MoveIt 2 are intentionally not launched (RAM budget); waypoint tracking is a pure-pursuit follower with LiDAR avoidance, harvester arm kinematics are mocked by dwell time. The UAV is kinematic (no Physics node) and moves itself through its supervisor-enabled controller.
- Fleet cap: `ARCADIA_MAX_ACTIVE_ROBOTS=2`. The AGV boots powered-down in storage and is woken only for tow recoveries.
- `scripts/dev_backend.sh` runs the backend natively against the compose Postgres/Redis for fast iteration; the Vite dev server proxies `/api` and `/ws` to it.
