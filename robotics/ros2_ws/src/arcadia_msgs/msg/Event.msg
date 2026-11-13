# Domain event from the simulation (supervisor or robot controllers)
builtin_interfaces/Time stamp
string kind          # cell_observed | seeded | weeded | harvested | mission_started | mission_complete | mission_failed |
                     # fault_detected | fault_cleared | tow_attached | tow_complete | charging_started | charged | world_loaded | robot_power
string robot_id
string h3
string mission_id
string severity      # INFO | WARN | ERROR
string payload_json  # free-form JSON object
