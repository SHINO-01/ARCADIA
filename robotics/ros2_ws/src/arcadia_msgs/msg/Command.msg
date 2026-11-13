# Backend -> simulation command
string command_id
string robot_id      # empty for world-level commands
string kind          # mission | cancel | return_home | chaos | clear_fault | world_load | set_active | tow | pause | resume | set_speed
string payload_json  # JSON object (mission waypoints, fault params, world config, ...)
