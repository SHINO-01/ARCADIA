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
├── frontend/       React 19 + Vite + MapLibre (Mapbox Draw) + H3 + "Altara" 60 fps canvas swarm renderer
├── robotics/       Native ROS 2 workspace: arcadia_msgs, arcadia_sim (Webots controllers), arcadia_supervisor (C++), arcadia_bringup
├── infra/caddy/    Reverse proxy (SPA, /api, /ws, /rosbridge, /webots-stream) with internal TLS on :8443
├── docker-compose.yml   caddy · api · worker · beat · web · postgres (TimescaleDB) · redis
└── scripts/        dev_backend.sh (native backend), seed_demo.py (demo farm)
```

## Quick start

```bash
# 1. ROS 2 Lyrical (once, needs sudo)
sudo bash robotics/scripts/install_ros2_apt.sh

# 2. Web stack (Docker)
cp .env.example .env            # optional: VITE_MAPBOX_TOKEN for Mapbox imagery (Esri imagery is the default)
make up                         # http://localhost:8080  (API docs: /api/docs)

# 3. Native robotics — Webots R2025a as a user-space tarball (the snap cannot open a Wayland display)
bash robotics/scripts/install_webots_tarball.sh     # -> ~/opt/webots, sets WEBOTS_HOME in ~/.bashrc
make robotics-build
make sim                        # Webots + supervisor + uav-1/ugv-1/agv-1 + rosbridge :9090 + diagnostics

# 4. Demo farm (or draw one in the dashboard: Plan → Draw boundary → Generate → Plan → Activate)
make seed-demo
```

Without `make sim` the backend's **headless digital twin** drives the whole platform (same wire
protocol), and the bridge hot-swaps to ROS 2 as soon as rosbridge on :9090 becomes reachable.

### Native backend for development
```bash
docker compose up -d postgres redis
scripts/dev_backend.sh start      # API :8000 + Celery worker + beat; logs in .run/
cd frontend && npm run dev        # Vite :5173 proxies /api and /ws to :8000
```

## Mission-control UI

The dashboard is grouped into five views (left rail, keys `1`–`5`):

| View | What it is for |
|---|---|
| **Plan** | Draw a boundary (built-in tool: click vertices, click the first point / double-click / `Enter` to close, `⌫` undo, `Esc` cancel), tessellate to H3, plan infrastructure, activate. The refresh button starts a **fresh session** (deletes every farm, mission and incident). |
| **Operations** | Live map + fleet roster + RTS command bar. |
| **3D Twin** | Embedded Webots W3D stream with camera presets (farm overview / follow a unit). |
| **Alerts** | Incidents, Chaos Engine (scenario presets, random interval, manual injection) and the event stream. |
| **Analytics** | Field KPIs, microgrids, yields, fleet telemetry, dispatch history. |

**RTS unit control (Operations):** click a unit to select · ctrl/shift+click to add to the selection · right-click the map to move the selection · shift+right-click to add route waypoints, then **Patrol** (looping) or **Go** · `S` stop · `H` nearest dock · `Esc` clear. Operator orders put a unit under manual control; **Auto** hands it back to the dispatcher. A UAV on patrol keeps surveying the cells it overflies.

**Infrastructure planning:** K-Means++ charging docks (solar/wind), robot storage, a **repair station**, a grid of **travel roads** (row roads every *N* hex rows plus headland cross roads at every bed boundary) and **crop beds** (blocks between roads, sliver beds merged). Road spacing, bed width, orientation (E–W / N–S), dock count and crop rotation are adjustable; re-planning a live farm respawns it in Webots. Ground vehicles route over the roads (Dijkstra, crop cells cost 6× a road cell) and only cut across a bed when the detour would be disproportionate.

**Stranding scenarios:** motor jam, battery collapse and **terrain entrapment** (waterlogged beds are drawn as hazards on the map and in Webots; ground vehicles crossing them can bog down, and the supervisor pins the chassis until it is winched). Tows drive the roads to the unit, winch it and deliver it to the **repair station**. Low-battery units head for the **nearest** charging dock, decided by route length and remaining range.

## Layer map (spec → implementation)

| Spec | Where |
|---|---|
| Mapbox Draw geo-selection → GeoJSON | `frontend/src/components/FarmMap.tsx` (MapLibre + `@mapbox/mapbox-gl-draw`) |
| Uber H3 hex mapping (auto resolution) | `backend/app/services/h3_engine.py` |
| K-Means dock/storage planning | `backend/app/services/planner.py` (numpy k-means++, travel-latency metrics) |
| World-config JSON payload | `backend/app/services/world_builder.py` → `GET /api/farms/{id}/world` |
| C++ Webots Supervisor spawner | `robotics/ros2_ws/src/arcadia_supervisor/` (terrain, hex crops, docks, storage, robots, ground truth, tow mechanics) |
| Robot models + controllers | `arcadia_sim/protos/*.proto`, `arcadia_sim/arcadia_sim/robot_controller.py` (pure-pursuit + LiDAR avoidance, torque-coupled battery) |
| Mock CV (ripeness/weeds) | Supervisor proximity events `cell_observed` (UAV sensor radius) |
| ROSbridge translation | `arcadia_bringup` launches `rosbridge_websocket`; backend client `backend/app/bridge/rosbridge_client.py` |
| Telemetry throttling | `arcadia_sim/arcadia_sim/telemetry_throttle.py` (50 Hz odom → 5 Hz `/arcadia/telemetry`) |
| diagnostic_aggregator | `arcadia_sim/config/diagnostics.yaml` → `/diagnostics_agg` → fleet OK/WARN/ERROR |
| FastAPI + WebSocket ingestion | `backend/app/main.py`, `backend/app/api/telemetry.py` (`/ws/telemetry`) |
| Celery + Redis dispatch | `backend/app/workers/tasks.py` (`auto_dispatch_tick`, `dispatch_mission`) |
| Chaos Engine | `backend/app/services/chaos.py` + `chaos_tick`; faults honoured by both sims |
| Time-series persistence | TimescaleDB hypertables `telemetry`, `energy_log` (`backend/alembic/versions/0001_initial.py`) |
| 60 fps canvas map ("Altara") | `frontend/src/altara/renderer.ts` |
| Split-screen Webots stream | `frontend/src/components/WebotsView.tsx` + `public/webots-viewer.html` (W3D stream on :1234) |
| Click-to-dispatch override | Fleet panel → arm → click hex → `POST /api/fleet/{id}/dispatch` (pauses auto for that robot) |
| Maintenance modal + AGV tow | `IncidentModal` → `POST /api/incidents/{id}/recover` → Celery `recovery_protocol` |
| Caddy HTTPS/WebSocket routing | `infra/caddy/Caddyfile` |

## Verified end to end (2026-11-14, IdeaPad Slim 3)
- Headless twin: farm → H3 grid → K-Means plan → activate → automated survey/seed/weed missions, TimescaleDB telemetry, chaos faults, AGV tow recovery. `make backend-test` passes.
- Native stack: Webots R2025a (tarball) + ROS 2 Lyrical — supervisor spawns the farm from the backend world config, three extern controllers attach, 50 Hz odometry throttled to 5 Hz `/arcadia/telemetry`, `diagnostic_aggregator` roll-ups, rosbridge ↔ FastAPI hot-swap (the backend switches from headless twin to ROS automatically), dashboard embeds the W3D stream.
- Energy autonomy in the physics sim: torque-coupled drain (wheel torque × angular velocity), passive solar skins fed by the supervisor's irradiance broadcast, and a 22% reserve reflex — the Scout UAV abandoned a survey at 22%, flew to dock-1 and charged, without the backend's help.
- Chaos → recovery in the physics sim: `motor_jam` injected into the Seeder UGV → `fault_detected` → interruptive modal → Logistics AGV powers up in storage, drives ~70 m (pure-pursuit + LiDAR), winches the UGV (supervisor drags it), parks it on the dock pad, fault cleared + recharge, incident resolved in 76 s, AGV powers back down.

## Design notes / limits
- The bridge runs in **auto** mode: headless twin until rosbridge appears, then ROS 2; if the native stack stops it falls back again. On any controller restart the robot sends `robot_online`, which fails its in-flight missions and closes its incidents so state never drifts; a Celery watchdog also times out silent missions.
- Nav2/MoveIt 2 are intentionally not launched (RAM budget); waypoint tracking is a pure-pursuit follower with LiDAR avoidance, harvester arm kinematics are mocked by dwell time. The UAV is kinematic (no Physics node) and moves itself through its supervisor-enabled controller.
- Fleet cap: `ARCADIA_MAX_ACTIVE_ROBOTS=2`. The AGV boots powered-down in storage and is woken only for tow recoveries.
- `scripts/dev_backend.sh` runs the backend natively against the compose Postgres/Redis for fast iteration; the Vite dev server proxies `/api` and `/ws` to it.
