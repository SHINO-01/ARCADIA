# Authoritative per-robot state published by its controller (~2 Hz); the telemetry throttle
# merges it into the compact Telemetry frames so status never drifts from the simulation.
builtin_interfaces/Time stamp
string robot_id
string type
string status        # idle | active | charging | fault | stranded | towing | towed | offline | returning
bool active
string mission_id
string fault         # empty when healthy, else motor_jam | battery_drop | network_latency | sensor_glitch
float64 latency_ms
float64 motor_temp_c
float64 cpu_load
