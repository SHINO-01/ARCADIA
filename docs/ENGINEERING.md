# ARCADIA: Engineering Guide

How the whole system works, from drawing a field on a satellite map to a wheel motor turning in Webots,
written so that someone new to the project (or to robotics) can follow it end to end. The two
parts people ask about most get the most space: **the Webots simulation** (section 6) and **how the
browser dashboard moves robots on the ground** (section 8).

The system diagram (animated, draw.io) is [docs/arcadia-system.drawio](arcadia-system.drawio). Open
it in diagrams.net or the draw.io VS Code extension; the connectors animate in the direction data flows.

---

## Contents

1. [What ARCADIA is](#1-what-arcadia-is)
2. [The system at a glance](#2-the-system-at-a-glance)
3. [Ground rules and why they shape the design](#3-ground-rules-and-why-they-shape-the-design)
4. [Coordinates, the field layout and the track network](#4-coordinates-the-field-layout-and-the-track-network)
5. [From a polygon on a map to a live farm](#5-from-a-polygon-on-a-map-to-a-live-farm)
6. [The Webots simulation in depth](#6-the-webots-simulation-in-depth)
7. [The bridge: where the cloud meets ROS 2](#7-the-bridge-where-the-cloud-meets-ros-2)
8. [How a click in the browser moves a robot](#8-how-a-click-in-the-browser-moves-a-robot)
9. [Autonomy: the dispatcher, campaigns and the virtual fleet](#9-autonomy-the-dispatcher-campaigns-and-the-virtual-fleet)
10. [Faults, the Chaos Engine and tow recovery](#10-faults-the-chaos-engine-and-tow-recovery)
11. [The agronomic world model and its two timelines](#11-the-agronomic-world-model-and-its-two-timelines)
12. [Data: Postgres, TimescaleDB and Redis](#12-data-postgres-timescaledb-and-redis)
13. [The dashboard](#13-the-dashboard)
14. [Deployment and operations](#14-deployment-and-operations)
15. [Testing](#15-testing)
16. [Known limitations and open gaps](#16-known-limitations-and-open-gaps)
17. [Glossary](#17-glossary)
18. [Code map](#18-code-map)

---

## 1. What ARCADIA is

ARCADIA is an **agricultural digital twin with a robot fleet**. You draw a field boundary on a
satellite map; the backend analyses the site (existing roads and obstacles from imagery, ten years of
climate, the soil), lays out crop beds and machine tracks optimised for that exact shape, picks a crop
by simulating a season of each candidate, and then brings the farm to life:

* an **agronomic world model** simulates weather, soil water, crop growth, weeds, disease and yield
  for every bed section, one day at a time;
* a **robot fleet** (a scout drone and ground rovers, with a tow vehicle on standby) surveys the
  crop and does the field work: sowing, side-dressing nitrogen, weeding, spraying, harvesting;
* a **web dashboard** shows all of it live and lets an operator take over any robot, handle
  breakdowns and run what-if scenarios.

The robots live in one of two simulators that speak the same protocol:

| Simulator | What it is | When it runs |
|---|---|---|
| **Webots + ROS 2** | A physics simulation (wheels, torque, collisions, LiDAR, GPS, IMU) of the farm and fleet, with one ROS 2 node per robot. Rendered in 3D and streamed into the dashboard. | When `make sim` runs natively on an Ubuntu x86-64 desktop. |
| **Headless twin** | A lightweight kinematic fleet simulator inside the backend process. Same commands in, same telemetry and events out. | Whenever Webots is not running: laptops, CI, cloud servers. |

The backend picks between them automatically and hot-swaps when Webots comes up or goes away
(section 7), so the dashboard never needs to know which one is driving.

---

## 2. The system at a glance

```
 Browser (anywhere)                                    Ubuntu host
 ┌──────────────────────────┐   HTTPS / WSS    ┌──────────────────────────── Docker ─────────────────────────────┐
 │ React dashboard          │ ───────────────▶ │ Caddy :8080/:8443                                                │
 │  MapLibre + Altara canvas│                  │  /         -> web   (static React build)                         │
 │  zustand stores          │ ◀─────────────── │  /api, /ws -> api   (FastAPI + world engine + bridge manager)    │
 │  Webots W3D viewer iframe│                  │  /rosbridge, /webots-stream -> host :9090 / :1234                │
 └──────────────────────────┘                  │                                                                  │
                                               │ api ── Redis (pub/sub + fleet cache) ── worker / beat (Celery)   │
                                               │  └──── Postgres + TimescaleDB (farms, missions, telemetry, ...)  │
                                               └───────────────┬──────────────────────────────────────────────────┘
                                                               │ rosbridge WebSocket (JSON) :9090
                                               ┌───────────────▼──────────── native ──────────────────────────────┐
                                               │ ROS 2 Lyrical: rosbridge, telemetry throttle, diagnostics,       │
                                               │                world fetcher, rosapi                             │
                                               │ Webots R2025a: C++ supervisor + 3 Python robot controllers       │
                                               │                W3D streaming server :1234                         │
                                               └──────────────────────────────────────────────────────────────────┘
```

### Processes and ports

| Process | Where | Port | Role |
|---|---|---|---|
| `caddy` | Docker | 8080 (http), 8443 (https) | Single entry point; reverse proxy for everything |
| `web` | Docker | 80 (internal) | Static React build served by its own Caddy |
| `api` | Docker | 8000 (internal) | FastAPI REST + `/ws/telemetry`; runs the **world engine** and the **bridge manager** in-process |
| `worker` | Docker | – | Celery worker: mission dispatch, recovery, chaos, energy, persistence |
| `beat` | Docker | – | Celery beat: the periodic schedule |
| `postgres` | Docker | 55432 → 5432 (localhost only) | PostgreSQL 17 + TimescaleDB |
| `redis` | Docker | 6379 (localhost only) | Pub/sub channels, fleet cache, campaign queues |
| `webots-bin` | native | 1234 | Physics simulation and W3D streaming server |
| supervisor, 3 controllers | native | (IPC to Webots) | Extern Webots controllers that are also ROS 2 nodes |
| `rosbridge_websocket` | native | 9090 | JSON-over-WebSocket gateway into ROS 2 |
| `telemetry_throttle`, `diagnostic_aggregator`, `world_fetcher`, `rosapi` | native | – | ROS 2 helpers |

---

## 3. Ground rules and why they shape the design

The project was built to run on a mid-range laptop (Intel i5-13420H, 16 GB RAM, integrated
graphics, Ubuntu). Four hard rules came with it, and most architectural choices follow from them.

| Rule | Consequence in the design |
|---|---|
| **ROS 2 and Webots run natively, never in Docker.** Only the web stack is containerised. | The backend reaches ROS 2 over a network protocol (rosbridge on :9090) instead of linking ROS libraries. The containers reach the host through `host.docker.internal`. The optional sim agent (`scripts/sim_agent.sh`) starts the native simulation alongside Docker without containerising it. |
| **No heavy 3D assets**: primitives only (planes, cylinders, boxes, spheres). | The Webots world file is nearly empty. The supervisor builds the entire farm at runtime from a JSON world description, one batched import per layer (section 6.3). Robots are small PROTOs made of boxes and cylinders. |
| **No heavy ML / computer vision.** | Seeing the crop is **mocked by proximity**: when the scout drone's footprint covers a bed section, the supervisor emits `section_seen`, and the backend's sensor model turns that into a noisy NDVI / water-stress / weed / disease measurement of the simulated truth (sections 6.3 and 11). |
| **At most 2 active robots.** | The fleet is a scout UAV and a ground UGV; the Logistics AGV boots powered down and is woken only to tow a broken-down unit. `ARCADIA_MAX_ACTIVE_ROBOTS=2`. The farm's field work is shared with an auto-sized **virtual fleet** inside the world model, so a 15 ha field still gets sown on time (section 9). |

Two more decisions run through everything:

* **One wire contract for every simulator.** Commands in, telemetry and events out, defined once as
  ROS messages (`arcadia_msgs`) and mirrored as Pydantic models (`backend/app/schemas`). Webots, the
  headless twin and, in principle, real robots behind a rosbridge gateway are interchangeable.
* **Ground truth lives in the backend.** The crop, soil and weather state is owned by the world
  engine. Webots *renders* it and reports what robots did; it never owns the agronomy. This is why a
  farm keeps its state across Webots restarts and why the headless twin and Webots agree.

---

## 4. Coordinates, the field layout and the track network

### 4.1 One local flat plane

GPS coordinates are awkward for robotics maths, so everything physical uses a local **ENU** frame
(x = metres east, y = metres north, z = metres up) centred on the field's centroid
(`backend/app/services/geo.py`, `Origin`). The backend converts to latitude/longitude only at the
edges: when the browser sends map clicks, and when telemetry goes out to the map. Webots' world is
declared `coordinateSystem "ENU"`, so the same numbers are used inside the simulator without any
transformation.

### 4.2 Controlled-traffic layout

Mechanised farms protect soil by never driving on the growing area. ARCADIA lays the field out the
same way (`backend/app/services/layout.py`):

| Element | What it is |
|---|---|
| **Bed** | A long strip of crop running along the field's main axis. |
| **Section** | A bed cut into ~40 m pieces. The **unit of everything agronomic**: crop stage, observations, work, yield. A 15 ha field has a few hundred. |
| **Tramline** | A permanent dirt lane between beds, just wide enough for a rover. Ground robots work a section *from* the tramline beside it. |
| **Headland** | A track around the inside of the field boundary for turning. |
| **Cross alleys** | Tracks cutting across long beds so routes do not need to go all the way round. |
| **Yard pads and spurs** | Docks, robot storage and the repair station sit on pads outside the field, each joined to the headland by a short spur. |
| **Roads** | Existing roads found in the satellite image become farm tracks too. |

Every section also stores its **work passes**: the tramline segments alongside it, used to plan
seeding, weeding and spraying passes.

### 4.3 The track network and routing

All tracks (headland, tramlines, alleys, spurs, roads) are joined into a graph
(`backend/app/services/track_network.py`): line segments are split at every intersection ("noding"),
tiny near-misses from geometry rounding are healed, and **Dijkstra** finds the shortest path between
any two points. A point not on a track (a click in the middle of a bed, the position of a stranded
robot) is first **snapped** to the nearest point on a track.

Rule: **ground vehicles only ever drive on the track network.** The one exception is harvest
(section 9.2). The graph is shipped to the robots too (`world_config.track_graph`, nodes and edges),
so a robot can plan its own on-track moves (returning to a dock on low battery, joining a mission from
wherever it actually is) without asking the backend. The robot-side copy is `TrackGraph` in
`robotics/ros2_ws/src/arcadia_sim/arcadia_sim/behaviour.py`.

### 4.4 The world config (schema 4)

`backend/app/services/world_builder.py` turns a planned farm into one JSON document, served at
`GET /api/world/active` and `GET /api/farms/{id}/world`. It is the only thing the simulators need to
build the farm:

```json
{
  "schema": 4,
  "farm":   {"id": "...", "name": "North Field", "revision": 1731800000.0, ...},
  "terrain": {"min_x": -260, "max_x": 250, "min_y": -210, "max_y": 230, "width": 510, "height": 440, ...},
  "sections": [{"id": "AB-03", "bed": "AB", "crop": "tomato", "x": 12.4, "y": -40.1,
                "length": 40, "width": 1.6, "angle": 0.21, "polygon": [...], ...}, ...],
  "tracks":   [{"kind": "tramline", "points": [[x, y], ...], "width": 2.2}, ...],
  "hazards":  [{"id": "hz-1", "x": ..., "y": ..., "r": 3.5}],
  "track_graph": {"nodes": [[x, y], ...], "edges": [[0, 1], ...]},
  "infrastructure": [{"id": "dock-1", "kind": "solar_dock", "x": ..., "y": ...}, ...],
  "robots": [{"id": "uav-1", "type": "scout_uav", "home": "dock-1", "active": true,
              "x": ..., "y": ..., "z": ..., "spec": {"max_speed": 6.0, "sensor_radius_m": 9.0, ...}}, ...]
}
```

`farm.revision` changes whenever the farm is re-planned. The supervisor uses it to decide whether a
`world_load` is new (respawn) or a repeat (ignore).

---

## 5. From a polygon on a map to a live farm

The **Farm** view is a five-step wizard. Each step is an API call; the long one runs in the
background and streams progress over the dashboard socket.

1. **Boundary.** The operator searches for the place (OpenStreetMap search) and draws a polygon with
   the built-in MapLibre drawing tool. `POST /api/farms` stores it as GeoJSON plus its ENU polygon.
2. **Site analysis** (`backend/app/services/site_analysis.py`, about 5 s):
   * **Imagery** (`imagery.py`): satellite tiles at ~0.5 m/pixel are downloaded in parallel and
     analysed with classic digital image processing in numpy/scipy. Road likelihood from colour
     combined with a multi-scale **Hessian ridge filter** (roads are long bright or dark ridges),
     hysteresis thresholding and morphology, an elongation test, **Zhang-Suen thinning** to one-pixel
     skeletons, skeleton tracing and Douglas-Peucker simplification into polylines, and widths from
     the distance transform. Buildings, water and tree cover are classified as obstacles.
   * **Climate** (`agro/climate.py`): ten years of ERA5 daily weather for the field from Open-Meteo,
     fitted into a stochastic weather generator.
   * **Soil**: SoilGrids topsoil properties for the location.
3. **Adaptive layout** (`layout.py`): the croppable area is the field minus the headland, road
   corridors and obstacles. Each zone gets its own bed direction and offset, searched coarse-to-fine
   to maximise soil-weighted bed area minus a turning cost per bed end; concave fields are tried split
   at their reflex corners. Roads become farm tracks. K-means++ over the sections places the charging
   docks (`planner.py`).
4. **Crop choice** (`agro/forecast.py`): every candidate crop (maize, winter wheat, soybean,
   processing tomato) is simulated for a full season on this farm's soil and climate, many times
   with different weather (Monte Carlo). The operator picks from yield, margin and risk.
5. **Activate** (`POST /api/farms/{id}/activate`): the farm becomes the active farm, the robots are
   registered, the world engine loads the farm in **real-time mode**, and the bridge sends
   `world_load` to whichever simulator is running.

---

## 6. The Webots simulation in depth

Webots is an open-source robot simulator: it steps a rigid-body physics engine (ODE) at a fixed
timestep, simulates sensors and motors, and renders the scene. ARCADIA uses it in an unusual way:
the world file is almost empty, and the farm is built at runtime by code.

### 6.1 What `make sim` starts

`make sim` sources ROS 2 and the workspace, then runs
`ros2 launch arcadia_bringup arcadia.launch.py`
(`robotics/ros2_ws/src/arcadia_bringup/launch/arcadia.launch.py`). The launch file starts processes
in a staggered order, because Webots must be listening before controllers connect to it:

| Time | Process | Notes |
|---|---|---|
| 0 s | **Webots** (`webots --mode=realtime --batch --stream --port=1234 arcadia_farm.wbt`) | `--stream` starts the W3D streaming server used by the dashboard. With `gui:=false` it also gets `--minimize --no-rendering`. |
| 2 s | **rosbridge_websocket** (:9090), **rosapi**, **diagnostic_aggregator**, **telemetry_throttle** | The backend's way in, plus helpers. |
| 6 s | **supervisor** (C++) | Respawned automatically if it crashes (`respawn=True`, 3 s delay). |
| 9 s | **robot_controller** × 3 (Python: `uav-1`, `ugv-1`, `agv-1`) | One process per robot, also respawned. |
| 12 s | **world_fetcher** | Pulls the active farm from the backend and publishes `world_load`. |

**Extern controllers.** In a normal Webots project, Webots starts each robot's controller program
itself. Here every robot's `controller` field is `"<extern>"`: Webots waits for an external process
to attach. The launch file starts each controller with `WEBOTS_CONTROLLER_URL=ipc://1234/<robot-name>`,
which tells the Webots library which robot in which Webots instance to bind to. This lets each
controller be a normal ROS 2 node, started, logged and respawned by `ros2 launch`.

**Display.** Webots needs an X display even when minimised. On GNOME/Wayland the launch file finds
the Xwayland auth cookie under `/run/user/<uid>/` and sets `DISPLAY` so Webots also starts from
shells outside the desktop session. The Webots snap cannot open the display on this setup, which is
why `robotics/scripts/install_webots_tarball.sh` installs the official tarball to `~/opt/webots`.

### 6.2 The world file

`robotics/ros2_ws/src/arcadia_sim/worlds/arcadia_farm.wbt` contains only fixed scaffolding:

* `WorldInfo`: ENU coordinates, `basicTimeStep 32` (the physics steps every 32 ms), 30 FPS, Coulomb
  friction 1.2 for wheel/ground contact.
* `DEF VIEWPOINT`, `DEF SKY` (a plain background colour, no HDR textures to stream),
  `DEF SUN` (directional light), `DEF FOG`: named so the supervisor can drive them from the weather model.
* `DEF GROUND`: a 600 × 600 m plane.
* `DEF SUPERVISOR`: an invisible robot with `supervisor TRUE` and an extern controller, the C++ supervisor.
* `DEF FARM Group { children [ ] }`: the empty slot the supervisor fills.
* `EXTERNPROTO` imports for `ArcadiaUAV`, `ArcadiaUGV`, `ArcadiaAGV` (`arcadia_sim/protos/`).

### 6.3 The supervisor (C++)

`robotics/ros2_ws/src/arcadia_supervisor/src/supervisor.cpp` is both a **Webots Supervisor**
(a controller allowed to read and modify any node in the scene) and an **rclcpp node**. Its main loop
steps Webots, services ROS callbacks, and runs per-robot logic:

```cpp
while (sup_.step(32) != -1 && rclcpp::ok()) {
  rclcpp::spin_some(node);                  // handle Commands / Events that arrived
  if (!world_.loaded) continue;
  for (auto &[id, r] : world_.robots) {
    if (r.type == "scout_uav" && altitude > 1.5) vision_.scan(r, sensor_radius);  // mock CV
    if (r.towing && r.tow_attached) dragTowed(r);                                  // tow mechanics
  }
}
```

It subscribes to `/arcadia/command` and `/arcadia/event`, and publishes on both.

**Commands it handles**

| Command | What happens |
|---|---|
| `world_load` | Parse the world config. If the same farm and `revision` are already loaded, ignore it. Otherwise clear the `FARM` group and **spawn the farm** (below), step once to let physics settle, emit `world_loaded`, and point the camera at the field. |
| `field_state` | `{"s": {"AB-03": [height, r, g, b, weeds], ...}}`: the world model's crop, rendered. Each listed section's box gets the new height and colour; its weed marker is shown or hidden. Sent as **deltas** (only sections whose look changed). |
| `weather` | Sun elevation and azimuth → `SUN.direction`; light intensity; `SKY.skyColor` and luminosity; `FOG.visibilityRange` in heavy rain; and whether tracks are wet. |
| `set_view` | Camera follow: sets `VIEWPOINT.follow` to a robot's name (mounted or tracking shot), or back to the overview. |
| `set_active` | Power flag. A powered-down robot is parked on a ring slot around its home pad. |

**Spawning the farm** (`world_spawner.cpp`). Everything is a primitive. To keep scene construction
fast, each layer is built as one VRML string and imported once with
`importMFNodeFromString` (one import for all tracks, one for all hazards, one for all beds, one for
all weed markers) instead of hundreds of individual imports:

* terrain plane and fence from the config's bounding box;
* tracks as flat boxes along each track segment;
* each **bed section as one box** (later resized and recoloured by `field_state`), plus a small weed
  marker above it, hidden while the section is clean;
* hazard discs (waterlogged low spots);
* docks, storage and the repair station as small structures on their pads;
* robots as PROTO instances: `ArcadiaUAV { name "uav-1" ... }`, `ArcadiaUGV { ... customData "seeder_ugv" }`,
  `ArcadiaAGV { ... }`. The `name` is the robot id (that is how the extern controller finds its robot)
  and `customData` carries the robot type.

The supervisor keeps a `World` struct (`world_state.hpp`) mapping section ids, robots and
infrastructure to their Webots nodes so later commands can find them.

**Mock computer vision.** `MockVision::scan` (`mock_vision.cpp`) runs every step for a scout UAV
flying above 1.5 m. Any section whose centre lies within `sensor_radius + section_length / 2` of the
drone (9 m radius by default) and that has not been seen in the cooldown window produces:

```json
{"kind": "section_seen", "robot_id": "uav-1", "section": "AB-03",
 "payload": {"section": "AB-03", "distance_m": 6.2, "altitude_m": 6.0}}
```

No pixels are involved. The backend's sensor model (`backend/app/agro/observe.py`) turns that event
into a **noisy measurement of the simulated truth**: NDVI, crop water stress (CWSI), weed cover,
disease, moisture, and a yield estimate once the crop is flowering. That gives the dashboard and the
anomaly detectors realistic, imperfect data, at zero GPU cost.

**Events it reacts to**

| Event | Reaction |
|---|---|
| `robot_online` | A controller that (re)started after the farm was spawned missed `world_load`: the supervisor **replays** the current world on `/arcadia/command`, so the robot learns its home dock, the docks and the track graph. |
| `tow_attached` | Starts dragging the target robot: every step it is placed 2.1 m behind the AGV, aligned with it, physics reset. |
| `tow_complete` | Stops dragging; the towed robot is left where it was delivered (the repair station apron). |
| `fault_detected` with `stuck_terrain` | Records the chassis position so it can be pinned in place. See section 16 for a gap here. |
| `fault_cleared` | Unpins; after a recharge the unit is left on the pad it was serviced at. |

**Why the supervisor does not own the crop.** Earlier versions grew crops inside the supervisor.
The current design moved all agronomy to the backend world model, so the supervisor only renders
`field_state` and reports what robots did. Work events (`seeded`, `harvested`, ...) come from the
robot controllers, go to the backend, change the model there, and come back as `field_state`.

### 6.4 Robot controllers (Python)

`robotics/ros2_ws/src/arcadia_sim/arcadia_sim/robot_controller.py`: one process per robot. Each is a
Webots controller (it constructs `controller.Supervisor()` and steps the simulation) and an `rclpy`
node at the same time.

**Devices.** GPS, IMU (and gyro/accelerometer when the robot has physics). Ground robots have four
wheel motors (`fl_wheel`, `fr_wheel`, `rl_wheel`, `rr_wheel`) in velocity mode with torque feedback
enabled, and a LiDAR. The UAV has four propellers that spin for show; it is **kinematic** (no physics
node, to stay cheap on integrated graphics), so its controller moves its own node directly. That is
why every robot runs as a supervisor-enabled controller.

**ROS interfaces** (topic names use underscores: `uav-1` → `/arcadia/uav_1/...`):

| Direction | Topic | Type | Rate |
|---|---|---|---|
| out | `/arcadia/<robot>/odom` | `nav_msgs/Odometry` | 50 Hz |
| out | `/arcadia/<robot>/imu` | `sensor_msgs/Imu` | 50 Hz |
| out | `/arcadia/<robot>/battery` | `arcadia_msgs/Battery` | 2 Hz |
| out | `/arcadia/<robot>/state` | `arcadia_msgs/State` | 2 Hz (authoritative status) |
| out | `/diagnostics` | `diagnostic_msgs/DiagnosticArray` | 2 Hz |
| out | `/arcadia/event` | `arcadia_msgs/Event` | on change |
| in | `/arcadia/command` | `arcadia_msgs/Command` | backend commands (filtered by `robot_id`) |
| in | `/arcadia/<robot>/cmd_vel` | `geometry_msgs/Twist` | manual teleoperation, 0.5 s hold |

**Command handling** (`on_command`):

| Command | Effect |
|---|---|
| `world_load` (broadcast) | Store the docks, yard pads and the `TrackGraph`; learn the home dock; take the power flag only if idle and healthy (a re-broadcast never wipes a running mission). |
| `mission`, `tow` | Ground robots first **join the tracks** (`join_tracks`): if the robot is not at the mission's first waypoint, an on-track route from where it really is gets prepended. Then `Behaviour.start_mission`. |
| `cancel` | Drop the mission. |
| `return_home` | Plan an on-track route to the nearest dock and go charge. |
| `chaos` | Inject a fault: `motor_jam` locks a wheel, `battery_drop` collapses the pack, `network_latency` delays odometry, `sensor_glitch` biases localisation, `stuck_terrain` immobilises. |
| `clear_fault` | Repair (optionally with a recharge). |
| `set_active` | Power on/off; announces `robot_power`. |
| `set_irradiance` (broadcast) | Sun strength for the solar skins on the battery model. |

**The behaviour state machine** (`behaviour.py`, pure Python, unit-testable, mirrored by the headless
twin). A mission is a list of waypoints plus a kind (`survey`, `seed`, `fertilise`, `weed`, `spray`,
`harvest`, `goto`, `patrol`, `charge`, `tow`). Each step, `tick()` returns the current target
waypoint or `None` (hold). `reached()` advances the index; patrols loop; a tow switches phase when it
reaches the pickup waypoint (`pickup_index`), emits `tow_attached`, waits 3 s for the winch, and
continues to the repair bay. When the last waypoint is done, `_complete()` emits the work event
(`seeded` with the section id, and so on) and `mission_complete`. A `charge` mission ends in
`charging`, which refills the battery and emits `charged`.

**Driving a ground robot** (`drive_ugv`):

* **Path following, not goal seeking.** The robot steers at a look-ahead point 1.5 m along the
  *current leg* (the line from the previous waypoint to this one), so any sideways error is pulled
  back onto the track centre line instead of persisting down a 200 m tramline (pure pursuit on the
  leg).
* **Pivot turns.** Heading error above 0.5 rad: stop and rotate in place, so the wheels stay on the
  track at junctions.
* **Speed.** Proportional to heading alignment and distance to the waypoint; capped at 0.5 m/s while
  a tool is engaged on a work pass.
* **Obstacles.** The LiDAR range image is checked in a ±15° cone straight ahead, 0.15–1.2 m. Something
  there means **stop and wait**, never swerve: on a tramline, swerving means driving onto the crop.
  The cone ignores the yard structures near pads, the target robot during a tow approach, and the
  crop during a harvest pass (that is what is being cut).
* **Arrival radius.** 0.8 m for a waypoint, 3 m for a tow pickup (winch cable reach), 4.5 m on a
  yard pad (the building is in the way of the exact centre).
* **Skid steer.** `(v, ω)` is converted to left/right wheel speeds for a 0.52 m axle and 0.11 m
  wheels.

**Flying the UAV** (`fly_uav`, `uav_integrate`): a velocity command towards the waypoint at the
cruise altitude (6 m), integrated and written straight to the node's translation and rotation.

**Battery physics.** Ground robots: mechanical power from wheel torque feedback × wheel speed,
scaled for skid-steer scrub and clamped, plus a speed-proportional drivetrain term and electronics
idle draw; solar skins add charge in sunshine. The UAV: a power curve on airspeed plus a hover
term. Battery time is compressed 12× against wall-clock so energy matters within a demo session.
Below **22 %** a robot abandons its mission and returns to the nearest dock **on its own**
(`low_battery_rth`, route along the tracks); at 0.5 % the battery collapses into a `battery_drop`
fault.

**Reporting.** About half a second after start the controller announces `robot_online` (with type,
power flag, battery and position). Parked, powered-down units keep reporting position at 2 Hz so the
dispatcher always plans from where they really are.

### 6.5 Telemetry throttle

Odometry at 50 Hz × 3 robots is too much to push through a WebSocket to a browser.
`telemetry_throttle.py` subscribes to every robot's odometry, battery and state, and publishes a
compact merged frame on **`/arcadia/telemetry`** (`arcadia_msgs/Telemetry`: position, heading,
speed, battery, motor temperature, CPU, latency, status, mission id) at **5 Hz per robot**, only for
robots whose data changed. Status comes from the controller's `State` message (authoritative); if a
controller stops publishing state for 5 s, the throttle falls back to inferring status from events.

### 6.6 Diagnostics

Each controller publishes `diagnostic_msgs` for its drive, battery, localisation and network.
`diagnostic_aggregator` (config `arcadia_sim/config/diagnostics.yaml`) rolls them up into
`/diagnostics_agg`, which the backend turns into per-robot OK / WARN / ERROR badges.

### 6.7 How the farm gets into Webots

Three paths deliver the world config, all producing the same `world_load` command:

1. **World fetcher** (`world_fetcher.py`): starts 12 s into the launch and polls
   `GET {api_url}/world/active` every 5 s until a farm is active, then publishes `world_load` once.
   This covers starting Webots after the web stack.
2. **Bridge manager**: when the backend connects to rosbridge, and whenever a farm is activated or
   re-planned, it publishes `world_load` itself. This covers starting the web stack after Webots,
   and live re-planning.
3. **Supervisor replay**: on any `robot_online`, the supervisor republishes the current world for
   controllers that restarted.

Because the supervisor ignores a `world_load` with the same farm id and revision, the repeats are
harmless. A **new revision** (the operator re-planned the layout) makes the supervisor clear and
respawn the farm; the robots are respawned too, their controllers reconnect and announce
`robot_online`, and the backend reconciles their missions.

### 6.8 Rendering the world model

The world engine (section 11) pushes two command streams to whichever simulator is running:

* every **2 s**: `field_state` deltas. Each section's look is computed from the model: soil colour
  from moisture (darker when wet, blue-grey when ponded), canopy cover from leaf area index blending
  towards green, yellowing for water stress, browning for disease, ripening colour per crop
  (tomatoes turn red, wheat gold), stubble after harvest; height from the crop model; a weed marker
  above the weed threshold. Only changed sections are sent.
* every **5 s**: `weather` (sun position computed from latitude, day of year and the farm's hour;
  light from irradiance; sky colour blending night → clear → overcast; fog in heavy rain; wet tracks
  after real rain) and `set_irradiance` for the robots' solar skins.

In **real-time mode** the sun in Webots therefore matches the actual sun at the farm.

### 6.9 The 3D view in the dashboard

Webots' `--stream` server speaks the W3D protocol: it streams the scene and its updates over a
WebSocket on :1234. Caddy proxies `/webots-stream` to it, so the browser reaches it on the dashboard's
own origin (and through a Cloudflare tunnel, if one is used). The **3D Twin** view embeds
`frontend/public/webots-viewer.html`, which loads Cyberbotics' viewer and connects to that socket.
Camera buttons call `POST /api/fleet/view?follow=uav-1`, which becomes a `set_view` command for the
supervisor.

---

## 7. The bridge: where the cloud meets ROS 2

### 7.1 rosbridge in one paragraph

`rosbridge_websocket` is a standard ROS package that exposes ROS 2 over a WebSocket using JSON. A
client sends `{"op": "advertise", "topic": ..., "type": ...}` before publishing,
`{"op": "publish", "topic": ..., "msg": {...}}` to publish, and
`{"op": "subscribe", "topic": ..., "type": ..., "throttle_rate": ..., "queue_length": ...}` to receive
`{"op": "publish", ...}` messages back. The ROS message is just a JSON object with the message's
fields. That is all the backend needs: it never links a ROS library.

`backend/app/bridge/rosbridge_client.py` is a small asyncio client: it reconnects with backoff,
re-advertises and re-subscribes after a reconnect, dispatches incoming messages to handlers, and
isolates handler exceptions so one bad message cannot kill the connection.

### 7.2 The bridge manager

`backend/app/bridge/manager.py` (`BridgeManager`) runs inside the `api` process. It is the **only
place the backend touches a simulator**:

| Responsibility | How |
|---|---|
| Choose a simulator | `ARCADIA_BRIDGE_MODE=auto` (default): probe rosbridge at start; if reachable use ROS, else start the headless twin and **keep probing**. `ros` or `headless` force one. |
| Hot-swap to ROS | When rosbridge connects, stop the twin, advertise `/arcadia/command`, subscribe to `/arcadia/telemetry`, `/arcadia/event` and `/diagnostics_agg` (throttled to 2 Hz), send `world_load`. |
| Fall back | When rosbridge disconnects: start the headless twin from the world's spawn state and **reconcile** (reset the fleet cache, fail in-flight missions, close stale incidents) so nothing claims a robot is mid-mission in a simulator that no longer exists. |
| Commands out | Subscribe to the Redis channel `arcadia:commands`; publish each command to the active simulator. For ROS that is `arcadia_msgs/Command` on `/arcadia/command` with the payload as a JSON string. |
| Telemetry in | Convert ENU → lat/lng, publish on Redis `arcadia:telemetry` (every frame), update the fleet cache at most ~2 Hz per robot, batch-insert into the `telemetry` hypertable (every 50 frames or 2 s). |
| Events in | Hand every event to `services/events.py` (section 8.3). |

### 7.3 The wire contract

Defined once in `robotics/ros2_ws/src/arcadia_msgs/msg/` and mirrored in `backend/app/schemas`:

| Message | Fields | Direction |
|---|---|---|
| `Command` | `command_id`, `robot_id` (empty = broadcast), `kind`, `payload_json` | backend → sim |
| `Telemetry` | `robot_id`, `type`, `status`, `x`, `y`, `z`, `heading`, `speed`, `battery_pct`, `battery_v`, `motor_temp_c`, `cpu_load`, `latency_ms`, `mission_id` | sim → backend |
| `Event` | `kind`, `robot_id`, `section`, `mission_id`, `severity`, `payload_json` | sim → backend |
| `State` | per-robot authoritative status | controller → throttle |
| `Battery` | pct, voltage, current, power, solar intake, charging | controller → throttle |

Command kinds: `mission`, `tow`, `cancel`, `return_home`, `chaos`, `clear_fault`, `set_active`,
`world_load`, `field_state`, `weather`, `set_irradiance`, `set_view`, `pause`, `resume`, `set_speed`.
Event kinds include `robot_online`, `mission_started`, `mission_complete`, `mission_failed`,
`section_seen`, `seeded`, `fertilised`, `weeded`, `sprayed`, `harvested`, `fault_detected`,
`fault_cleared`, `tow_attached`, `tow_complete`, `charging_started`, `charged`, `robot_power`,
`world_loaded`.

Payloads are JSON strings inside the ROS message so the contract can grow without rebuilding the
message package (rosbridge caches message classes, so a change to `arcadia_msgs` needs a full
`make sim` restart).

### 7.4 The headless twin

`backend/app/bridge/headless_sim.py` implements the same contract in-process at 10 Hz: kinematic
robots following the same waypoints and track rules, the same mission state machine and fault
handling, the same proximity-based `section_seen` for the UAV, and the same work events. It is what
lets the project run on any machine (or a free ARM cloud VM, where Webots does not exist) with
every dashboard feature except the 3D view. `backend/tests/test_twin.py` drives it through surveys,
seeding, weeding, harvests, patrols, return-home and a full tow recovery, and checks every telemetry
frame against the bed geometry.

---

## 8. How a click in the browser moves a robot

This is the full path of an operator order, with the code at each hop. The example: the operator
selects the ground rover on the **Operations** map, shift+right-clicks two points to make a route and
presses **Go**.

### 8.1 Command path: browser → robot

```
Browser ──REST──▶ FastAPI ──Celery──▶ worker ──Redis pub/sub──▶ bridge ──rosbridge──▶ ROS 2 ──▶ controller ──▶ wheels
```

1. **Browser.** The map click positions (lat/lng) are collected by the RTS command bar. Before
   sending, the dashboard calls `POST /api/fleet/ugv-1/route/preview`; the backend returns the real
   on-track path, drawn on the map so the operator sees where the robot will actually drive.
2. **API** (`backend/app/api/fleet.py`, `operator_route`): each click is converted to ENU, snapped to
   the track network, and routed leg by leg with Dijkstra (`dispatcher.ground_route`), starting from
   the robot's **live** position in the Redis fleet cache. A drone gets the clicks directly at 6 m.
   The API stores a `Mission` row (`status=queued`, `source=operator`), marks the robot as under
   manual control (the autonomous dispatcher leaves it alone until **Auto** is pressed), publishes a
   `cancel` for whatever it was doing, and enqueues the Celery task `dispatch_mission`.
3. **Celery worker** (`backend/app/workers/tasks.py`, `dispatch_mission`): marks the mission
   `dispatched`, builds the payload (`mission_id`, `kind`, `waypoints`, section, extras) and
   publishes a `Command(kind="mission", robot_id="ugv-1", payload=...)` on the Redis channel
   **`arcadia:commands`**. It also updates the fleet cache and tells dashboards the mission was
   dispatched.
4. **Bridge manager** (`_command_loop` in the `api` process) receives it from Redis and publishes it
   through rosbridge to **`/arcadia/command`** as an `arcadia_msgs/Command` whose `payload_json` holds
   the waypoints.
5. **rosbridge** turns the JSON into a real ROS 2 message on the host.
6. **Robot controller** for `ugv-1` (`on_command`, which ignores commands for other robots): joins
   the tracks from its actual position if needed, and starts the mission in its behaviour state machine,
   which emits `mission_started`.
7. **Every 32 ms** the controller steps Webots: the behaviour returns the current waypoint,
   `drive_ugv` computes `(v, ω)` with pure pursuit and the LiDAR cone, and sets the four wheel motor
   velocities. ODE moves the rover.

Typical latency from click to wheels: tens of milliseconds over a local network; the Celery hop and
Redis are each a few milliseconds.

### 8.2 Telemetry path: robot → browser

```
controller (50 Hz odom) ─▶ throttle (5 Hz) ─▶ rosbridge ─▶ bridge ─▶ Redis arcadia:telemetry ─▶ /ws/telemetry ─▶ browser store ─▶ Altara canvas (60 fps)
```

1. The controller publishes odometry at 50 Hz and state/battery at 2 Hz.
2. The telemetry throttle merges them into one `Telemetry` frame per robot at 5 Hz.
3. The bridge manager receives it from rosbridge, adds latitude/longitude, publishes it on Redis
   `arcadia:telemetry`, updates the fleet cache and queues it for the TimescaleDB `telemetry` table.
4. **`/ws/telemetry`** (`backend/app/api/telemetry.py`): each connected browser has one WebSocket.
   The server subscribes to `arcadia:telemetry` and `arcadia:dashboard` in Redis and forwards every
   message. On connect it sends a snapshot (fleet, bridge status, energy). This also works across API
   replicas, because Redis is the fan-out.
5. **Browser** (`frontend/src/store/live.ts`): a zustand store keeps the latest two samples per robot.
6. **Altara** (`frontend/src/altara/renderer.ts`): one `<canvas>` over the MapLibre map. Each animation
   frame **interpolates** every robot between its last two samples (position and heading), projects it
   through the map camera and draws the sprite, trail, battery arc and status ring. 5 Hz telemetry
   looks like smooth 60 fps motion, and robots never become DOM elements (no layout thrash).

### 8.3 Event path: what the robot did

Events (`mission_complete`, `seeded`, `section_seen`, `fault_detected`, ...) take the same road to
the bridge, then go through `backend/app/services/events.py`:

* mission lifecycle → `Mission` rows (`running`, `done`, `failed`) and the fleet cache;
* `section_seen` → the world engine's sensor model → updated section observations, pushed to the map;
* work events → the world engine changes the field (sow, fertilise, weed, spray, harvest), books the
  yield for what was actually ripe, releases the section's campaign claim, and pushes the new section
  state to dashboards; the new look reaches Webots on the next `field_state`;
* faults → an `Incident` row and an alert (section 10);
* everything compact goes out on `arcadia:dashboard` to every browser.

### 8.4 What "cloud" means here

The browser can be anywhere: on the same machine, on the LAN, or on the internet through a free
Cloudflare tunnel (`setup.sh --tunnel`), which forwards HTTPS and WebSockets to Caddy without opening
ports. The path above does not change; only the first hop gets longer. The web stack also runs on a
cloud VM with the headless twin (`setup.sh --cloud`), where the "robots on the ground" are the
twin's.

Extending this to physical robots means putting the same contract in front of them: a rosbridge (or
any gateway speaking `Command` / `Telemetry` / `Event`) on the robot's network, reachable by the
bridge manager. The backend would not change. This is a design property, not something the project
has run against hardware.

### 8.5 Other operator actions

| Action in the dashboard | API | Becomes |
|---|---|---|
| Click-to-dispatch onto a section | `POST /api/fleet/{id}/dispatch` | A work or survey mission built like the autonomous ones |
| Stop (`S`) | `POST /api/fleet/{id}/stop` | `cancel` |
| Nearest dock (`H`) | `POST /api/fleet/{id}/return-home` | A `charge` mission along the tracks to the nearest dock |
| Auto | `POST /api/fleet/{id}/resume-auto` | Clears manual control; the dispatcher may assign work again |
| Power | `POST /api/fleet/{id}/power?active=` | `set_active` |
| Camera follow (3D view) | `POST /api/fleet/view?follow=` | `set_view` |
| Pause / resume autonomy | `POST /api/missions/queue/pause` | Dispatcher stops creating missions |
| Confirm a tow | `POST /api/incidents/{id}/recover` | Recovery protocol (section 10) |
| Inject a fault | `POST /api/chaos/inject` (or a preset) | `chaos` command |

---

## 9. Autonomy: the dispatcher, campaigns and the virtual fleet

### 9.1 The loop

Celery beat runs **`auto_dispatch_tick` every 5 s** (`workers/tasks.py`). For each active robot that
is idle, healthy, not under manual control and without an open mission, it asks
`dispatcher.plan_for_robot` for a mission, stores it, and enqueues `dispatch_mission`. Queued missions
(for example a second tow while the AGV is busy) are released as soon as their robot becomes idle.
A **watchdog** every 30 s fails missions that went silent and flags units that hold a mission but
have not moved for 90 s (`mission_stalled`).

### 9.2 What each robot type does

* **Scout UAV**: surveys sections whose last observation is older than 5 minutes, up to 24 per flight,
  starting from the stalest and flown in lawn-mower order bed by bed, at 6 m.
* **Ground UGV**: takes field work from the world model's **campaign queues** in priority order
  (harvest, then spray and weed, then fertilise, then seed). Among the nearest six candidates it picks
  the one with the shortest on-track route, then **claims** the section in Redis
  (`arcadia:ops:claims`) so nobody else works it. The mission (`section_mission`) is: route over the
  tracks to the nearer end of the tramline beside the section, then the **work pass** along it
  (`pass_from` marks where the tool engages). **Harvest is the exception**: the pass turns onto the
  bed, runs down its centre line and comes back to the same tramline.

### 9.3 Campaigns and the virtual fleet

A 15 ha farm cannot be worked by one rover. The world engine (`services/operations.py`) therefore
plans the season's work as **campaigns** (which sections need sowing, side-dressing, weeding,
spraying or harvesting, decided by agronomy and by whether the soil is workable today), publishes
the queues to Redis (`arcadia:ops:pending`), and works them with a **virtual fleet** sized to the
farm (machine-hours per day × work rates per hectare). The physical robots take from the same
queues; their claims keep the virtual fleet off the sections they are heading for. To the
agronomy, a section seeded by the virtual fleet and one seeded by the Webots rover are the same
event.

---

## 10. Faults, the Chaos Engine and tow recovery

### 10.1 Faults

| Fault | Who | Effect | Needs a tow |
|---|---|---|---|
| `motor_jam` | ground robots | A wheel locks, motor temperature spikes | yes |
| `battery_drop` | all | Pack collapses (also happens naturally at ~0 %) | yes |
| `stuck_terrain` | ground robots | Wheels slip, unit immobilised | yes |
| `network_latency` | all | Odometry delayed, packet loss | no (soft) |
| `sensor_glitch` | all | GPS/IMU/LiDAR bias shifts localisation | no (soft) |

The **Chaos Engine** (`services/chaos.py`, beat every 10 s) waits its configured interval
(90 s by default), rolls its probability and injects a random allowed fault into a random eligible
robot as a `chaos` command. Faults only make sense per platform (a drone has no wheels to jam).

### 10.2 Recovery, step by step

1. The controller reports `fault_detected`. `events.py` opens an `Incident`; for a tow-requiring fault
   the robot becomes `stranded` and its mission fails. The dashboard raises a maintenance dialog.
2. **The operator confirms** (`POST /api/incidents/{id}/recover`). Nothing tows on its own. Soft faults
   are cleared remotely with `clear_fault` instead.
3. **`recovery_protocol`** (Celery) plans the tow: an on-track route from where the AGV **actually is**
   (live fleet cache, not the database) to the stranded unit, then from there to the repair station.
   `pickup_index` marks the hand-over waypoint. It powers the AGV on (`set_active`), which respects the
   2-robot cap because the stranded unit no longer counts as working, and dispatches a `tow` mission.
4. In Webots the AGV drives the tracks; within 3 m of the target it emits `tow_attached`. The
   supervisor starts **dragging** the stranded robot behind the AGV every physics step. The backend
   shows it as `towed`.
5. At the repair station the AGV emits `tow_complete`; the supervisor releases the robot on the apron.
   The backend clears the fault (with a recharge), resolves the incident and powers the AGV back down.

---

## 11. The agronomic world model and its two timelines

The world engine (`backend/app/services/world_engine.py`) runs inside the API process and owns the
farm's truth. The science lives in `backend/app/agro/`:

| Module | Method |
|---|---|
| `climate.py`, `weather.py` | ERA5 daily history → Richardson-type stochastic generator (Markov rain occurrence, gamma amounts, harmonic means, correlated residuals); FAO-56 Penman-Monteith reference evapotranspiration; hourly temperature and irradiance; scenario layer |
| `soil.py` | SoilGrids means plus Gaussian-random-field variability; Saxton & Rawls hydraulics; D8 terrain wetness index; SCS curve numbers; salinity; compaction on tracks only |
| `model.py` | Per section: FAO-56 root-zone water balance (runoff, run-on, ponding, waterlogging, automatic irrigation), thermal-time phenology, leaf area, radiation-use biomass, harvest index with heat/frost/drought penalties, nitrogen dilution, weed competition, disease spread to neighbours, salinity |
| `observe.py` | Sensor models for UAV sightings and soil probes |
| `anomalies.py` | Heatwave z-scores, SPI-30 drought, extreme rain, waterlogging, water stress, robust spatial NDVI outliers with cause inference, disease clusters, irrigation CUSUM |
| `forecast.py` | Monte Carlo season forecasts (yield P10/P50/P90, maturity, revenue) and crop comparisons |

**Timelines.** The engine runs the model on one of two separate timelines:

* **Real-time** (default, always on start): the farm clock is the wall clock at the farm, in local
  mean solar time (UTC + longitude/15 h), one second per second. Weather is generated once per day;
  the end of each day runs the model step, the anomaly detectors and the records. The state is
  snapshotted every minute; after downtime the twin catches up on missed days at up to 5 days per
  tick, quietly, then resynchronises the map and the forecast.
* **Simulation**: a sandbox forked from the real-time state, with its own snapshot and its own
  history (`mode` column on every world table, migration 0006). It can run at 1 day per 2 min down to
  1 day per 4 s, pause, restart from live, and take what-if scenarios (drought, heatwave, wet spell,
  frost, storm, pest outbreak, irrigation failure). Nothing done there changes the real-time history.

Every 2 s the engine pushes its state to dashboards and the `field_state` to the simulator; every
5 s the weather. Robot events feed back into it (section 8.3).

---

## 12. Data: Postgres, TimescaleDB and Redis

### 12.1 Tables (PostgreSQL 17 + TimescaleDB, migrations in `backend/alembic/versions/`)

| Table | Contents |
|---|---|
| `farms` | Boundary, world config (JSON), site analysis results, crop plan, status |
| `sections` | One row per bed section: geometry, work passes, stage, latest observations, soil |
| `infrastructure` | Docks, storage, repair station; capacity and storage |
| `robots` | Fleet registry and last durable state |
| `missions` | Every mission with waypoints, source (auto / operator / recovery), lifecycle times |
| `incidents` | Faults, tow requirement, state (open → acknowledged → recovering → resolved) |
| `telemetry` | **Hypertable**: every throttled frame (time, robot, position, speed, battery, temperature, CPU, status) |
| `energy_log` | **Hypertable**: microgrid readings per dock |
| `yields` | Harvest bookings per section, per timeline |
| `world_states` | World-engine snapshot per farm and timeline |
| `weather_daily`, `farm_daily` | One row per farm day per timeline |
| `anomalies` | Detected anomalies with method, value, threshold and action |

### 12.2 Redis

| Key / channel | Type | Purpose |
|---|---|---|
| `arcadia:commands` | channel | Everything going to the simulator (API, Celery and world engine → bridge) |
| `arcadia:telemetry` | channel | Throttled telemetry → every browser socket |
| `arcadia:dashboard` | channel | Fleet, missions, incidents, sections, world state → every browser socket |
| `arcadia:fleet` | hash | Live per-robot state (position, battery, status, mission, manual flag) |
| `arcadia:active_farm` | string | The farm the fleet is working |
| `arcadia:ops:pending` | string (JSON) | Campaign queues from the world model |
| `arcadia:ops:claims` | hash | Section → claiming robot, with a TTL check |
| `arcadia:world:now` | string (JSON) | Farm hour, irradiance, wind and time scale for the energy model |
| `arcadia:energy` | hash | Latest microgrid reading per dock |
| `arcadia:dispatch_paused`, `arcadia:chaos_config`, `arcadia:bridge_status` | strings | Switches and status |

Redis carries **live** state; Postgres keeps **durable** state. Celery uses Redis as its broker.

---

## 13. The dashboard

`frontend/` is React 19, Vite, Tailwind v4, MapLibre GL and zustand.

* **One WebSocket** (`/ws/telemetry`) multiplexes telemetry and dashboard events into two stores:
  `store/live.ts` (fleet, missions, incidents, energy, section overrides, event feed) and
  `store/world.ts` (clock, weather, KPIs, campaigns, anomalies, forecast). It reconnects with backoff
  and starts from a snapshot.
* **Views** (keys 1–6): Farm (setup wizard), World (world model), Operations (map and fleet),
  3D Twin (Webots stream), Alerts, Analytics.
* **Map**: MapLibre with satellite imagery; bed sections as GeoJSON layers recoloured by stage, NDVI,
  water, health, yield or crop; overlays for detected roads, obstacles, classification and soil
  rasters (served as PNGs by `/api/world/soil/{layer}.png`).
* **Altara** draws robots, trails and routes on a canvas above the map (section 8.2).
* **RTS control**: click to select, ctrl/shift-click to add, right-click to move, shift+right-click for
  waypoints, then Patrol or Go; `S` stop, `H` dock, `Esc` clear.
* The status bar carries the world clock with the Real-time / Simulation switch, and the link,
  simulator, telemetry rate, active robots, map fps and row count; the simulation sandbox adds its
  own control bar.

---

## 14. Deployment and operations

* **Compose** (`docker-compose.yml`, project name `arcadia`): `caddy`, `api`, `worker`, `beat`, `web`,
  `postgres`, `redis`, plus optional `tunnel` / `tunnel-named` profiles (Cloudflare). Health checks gate
  start-up order: Caddy waits for a healthy API and dashboard; worker and beat wait for the API.
  The API container runs `alembic upgrade head` on start.
* **Caddy** (`infra/caddy/Caddyfile`): `/api/*` and `/ws/*` → `api:8000`; `/rosbridge*` → host :9090;
  `/webots-stream*` → host :1234; everything else → `web`. Port 8443 serves the same with Caddy's
  internal certificate.
* **Setup**: `setup.sh` (one line; `--full` installs ROS 2 + Webots natively; `--sim-agent` opts into
  the agent; `--tunnel`, `--cloud`, `--demo`, `--port`, `--project`).
* **Running, stopping and clean-up**: see the README.
* **Hosting**: [docs/HOSTING.md](HOSTING.md) compares free options (your own PC + Cloudflare Tunnel
  runs everything; Oracle Cloud's free ARM VM runs the web stack with the headless twin).

---

## 15. Testing

`make backend-test` (27 tests, `backend/tests/`):

* `test_agro.py`: FAO-56 worked example for ET0, Saxton-Rawls hydraulics, realistic yields, SPI drought
  detection, forecast quantiles;
* `test_imagery.py`: road and obstacle detection on a synthetic scene;
* `test_routing.py`, `test_spatial.py`: track noding, healing and routing; layout geometry;
* `test_dispatch.py`: mission planning against the beds;
* `test_twin.py`: the headless twin end to end (survey, seed, weed, ripe and early harvest, goto,
  looped patrol, return-home, breakdown tow planned from a stale AGV position), checking every
  telemetry frame against the bed geometry so no ground robot ever crosses a bed outside harvest.

Frontend: `npx tsc -b` and `npx oxlint src`. The Webots stack is verified by running it (README,
"Verified end to end").

---

## 16. Known limitations and open gaps

* **Terrain entrapment is not wired up in Webots.** The supervisor has `terrainHazard()` (random
  bogging-down of ground robots crossing waterlogged spots after real rain) and `holdPinned()`
  (holding a bogged chassis still), but its main loop never calls them. In Webots, `stuck_terrain`
  therefore only happens when the Chaos Engine injects it, and the chassis is stopped by the controller
  (wheels at zero) rather than pinned by the supervisor. The headless twin is unaffected.
* **No Nav2 / MoveIt 2** (RAM budget): navigation is the pure-pursuit follower above with stop-and-wait
  obstacle handling; the harvester's arm is a dwell time.
* **The UAV is kinematic**: no aerodynamics; its controller sets its pose.
* **Mock vision only**: observations are a sensor model of the simulated truth, not image analysis.
* **Solar time, not civil time**: the real-time clock is local mean solar time at the farm's
  longitude (no time zones or equation of time), which is what the sun and the crop respond to.
* **Battery time is compressed** 12× in the robot controllers and the headless twin, while the farm
  clock runs 1:1 in real-time mode.
* **No authentication** on the dashboard or API: expose it publicly only behind something that adds it
  (for example Cloudflare Access on the tunnel).
* **Physical robots** are not integrated; the contract is designed for it (section 8.4).

---

## 17. Glossary

| Term | Meaning |
|---|---|
| ENU | Local East-North-Up frame in metres centred on the field |
| Section | ~40 m piece of a bed; the unit of agronomy and work |
| Tramline | Permanent lane between beds that machines drive on |
| Headland | Track around the inside of the field for turning |
| Work pass | Driving along a tramline beside a section with the tool engaged |
| Campaign | A season task (sow, side-dress, weed, spray, harvest) over a set of sections |
| Virtual fleet | Simulated machine-hours that work the campaigns alongside the physical robots |
| Supervisor | Privileged Webots controller that can create and modify scene nodes |
| Extern controller | Controller process started outside Webots and attached over IPC |
| rosbridge | JSON-over-WebSocket gateway into ROS 2 |
| Headless twin | The backend's in-process kinematic simulator |
| World config | JSON description of a planned farm that the simulators build from |
| `field_state` | Command carrying each section's rendered height, colour and weeds |
| Hot-swap | The backend switching between the headless twin and Webots at runtime |
| NDVI / CWSI | Vegetation index / crop water stress index (simulated measurements) |
| SPI-30 | 30-day standardised precipitation index (drought) |

---

## 18. Code map

```
backend/app/
  main.py                    FastAPI app; starts the bridge manager and the world engine
  api/                       REST routers: farms, fleet, missions, incidents, chaos, world, telemetry (+ /ws)
  bridge/manager.py          simulator selection, hot-swap, command/telemetry/event plumbing
  bridge/rosbridge_client.py asyncio rosbridge client
  bridge/headless_sim.py     in-process kinematic twin
  services/world_engine.py   world model runtime, timelines, field_state/weather pushes
  services/dispatcher.py     autonomous mission planning, work passes, on-track routes
  services/track_network.py  track graph, snapping, Dijkstra
  services/layout.py         controlled-traffic layout and adaptive optimisation
  services/imagery.py        satellite image processing (roads, obstacles)
  services/events.py         sim events -> missions, incidents, world model, dashboards
  services/chaos.py          Chaos Engine
  services/energy.py         dock microgrids
  workers/tasks.py           Celery tasks: dispatch tick, dispatch, watchdog, chaos, recovery, energy
  agro/                      weather, climate, soil, crop model, sensors, anomalies, forecasts
robotics/ros2_ws/src/
  arcadia_msgs/              Command, Telemetry, Event, State, Battery
  arcadia_supervisor/        C++ supervisor: spawner, mock vision, tow mechanics, rendering
  arcadia_sim/               robot controller, behaviour, telemetry throttle, world fetcher, PROTOs, world
  arcadia_bringup/           launch file, rosbridge config
frontend/src/
  App.tsx, components/       shell, map, panels, setup wizard, Webots view
  altara/renderer.ts         canvas robot renderer
  store/                     live and world stores
  views/                     World, Operations, Alerts, Analytics
infra/caddy/Caddyfile        routing
scripts/                     dev backend, demo farm, sim agent
```
