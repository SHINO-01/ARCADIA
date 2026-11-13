# Throttled per-robot telemetry sample (mirrors backend TelemetryFrame)
builtin_interfaces/Time stamp
string robot_id
string type          # scout_uav | seeder_ugv | cropcare_ugv | harvester_ugv | logistics_agv
string status        # idle | active | charging | fault | stranded | towing | towed | offline | returning
float64 x            # ENU metres (east)
float64 y            # ENU metres (north)
float64 z            # metres up
float64 heading      # radians, CCW from +x (east)
float64 speed        # m/s
float64 battery_pct
float64 battery_v
float64 motor_temp_c
float64 cpu_load     # 0..1
float64 latency_ms   # injected network latency (chaos)
string mission_id
