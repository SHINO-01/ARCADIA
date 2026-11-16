#!/usr/bin/env bash
# ARCADIA sim agent: makes the native robotics stack (Webots + ROS 2 fleet + rosbridge) follow the
# Docker web stack, so `docker compose up -d` brings the whole project to life and
# `docker compose down` puts it to sleep.
#
# Webots and ROS 2 stay native (they need the GPU, the display and every core); this agent runs as
# a systemd user service in your desktop session and only starts/stops `make sim`:
#
#   web stack healthy (GET /api/health)       -> start the simulation (once)
#   web stack gone for ARCADIA_SIM_GRACE_S     -> stop the simulation (a rebuild/restart does not)
#   simulation crashed while the stack is up  -> start it again
#   `make sim` already running by hand        -> leave it alone
#
# Usage
#   scripts/sim_agent.sh install     install + start the user service (arcadia-sim.service)
#   scripts/sim_agent.sh uninstall   stop + remove it
#   scripts/sim_agent.sh status      service state and what the agent is doing
#   scripts/sim_agent.sh logs        follow the agent + simulation log
#   scripts/sim_agent.sh run         the agent loop itself (what the service runs)
#
# Settings (.env): ARCADIA_SIM_GUI=true|false (Webots window or minimised, default true),
#                  ARCADIA_SIM_GRACE_S (default 45), HTTP_PORT (default 8080)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=arcadia-sim.service
UNIT_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$UNIT"
LAUNCH_PATTERN="^[^ ]*python[0-9.]* [^ ]*ros2 launch arcadia_bringup"  # the real launch, not a shell mentioning it

env_get() { # key default: value from .env (the stack's own settings)
  local v
  v="$(sed -n "s/^$1=//p" "$ROOT/.env" 2>/dev/null | tail -1)"
  echo "${v:-$2}"
}
log() { echo "$(date '+%F %T') sim-agent: $*"; }

stack_up() { curl -fsS -m 3 "http://localhost:$(env_get HTTP_PORT 8080)/api/health" >/dev/null 2>&1; }
sim_running() { pgrep -f "$LAUNCH_PATTERN" >/dev/null 2>&1; }

SIM_PID=""
start_sim() {
  if [[ ! -f "$ROOT/robotics/ros2_ws/install/setup.bash" ]]; then
    log "robotics workspace not built - run 'make robotics-build' (or setup.sh --full); staying on the headless twin"
    return 1
  fi
  local gui; gui="$(env_get ARCADIA_SIM_GUI true)"
  local target=sim; [[ "$gui" == "false" ]] && target=sim-headless
  log "web stack is up - starting the simulation (make $target)"
  # own process group, so stopping reaches ros2 launch, Webots and every controller
  setsid make -C "$ROOT" "$target" API_URL="http://localhost:$(env_get HTTP_PORT 8080)/api" &
  SIM_PID=$!
}
stop_sim() {
  [[ -n "$SIM_PID" ]] || return
  log "stopping the simulation"
  # the whole process group: make, ros2 launch, Webots, controllers, rosbridge
  kill -TERM -- "-$SIM_PID" 2>/dev/null  # not INT: background jobs of a script ignore it
  for _ in $(seq 1 20); do pgrep -g "$SIM_PID" >/dev/null || break; sleep 1; done
  if pgrep -g "$SIM_PID" >/dev/null; then
    log "still running after 20 s: $(pgrep -g "$SIM_PID" -l | awk '{print $2}' | sort -u | tr '\n' ' ')- killing"
    kill -KILL -- "-$SIM_PID" 2>/dev/null
  fi
  wait "$SIM_PID" 2>/dev/null
  SIM_PID=""
  log "simulation stopped"
}

run() {
  trap 'stop_sim; exit 0' TERM INT
  log "watching the web stack on :$(env_get HTTP_PORT 8080) (project $ROOT)"
  local down_since=0 backoff_until=0 noted_manual=0
  while true; do
    local now; now=$(date +%s)
    if stack_up; then
      down_since=0
      if [[ -n "$SIM_PID" ]] && ! kill -0 "$SIM_PID" 2>/dev/null; then
        wait "$SIM_PID" 2>/dev/null
        log "simulation exited - restarting in 10 s"
        SIM_PID=""; backoff_until=$((now + 10))
      fi
      if [[ -z "$SIM_PID" ]] && (( now >= backoff_until )); then
        if sim_running; then
          (( noted_manual )) || log "a simulation is already running (started by hand) - leaving it alone"
          noted_manual=1
        else
          noted_manual=0
          start_sim || backoff_until=$((now + 60))
        fi
      fi
    elif [[ -n "$SIM_PID" ]]; then
      (( down_since )) || down_since=$now
      if (( now - down_since >= $(env_get ARCADIA_SIM_GRACE_S 45) )); then
        log "web stack has been down for $((now - down_since)) s"
        stop_sim; down_since=0
      fi
    fi
    sleep 3 & wait $!
  done
}

install() {
  command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }
  mkdir -p "$(dirname "$UNIT_FILE")"
  cat > "$UNIT_FILE" <<EOF
[Unit]
Description=ARCADIA sim agent (native Webots + ROS 2 follow the Docker web stack)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=$ROOT/scripts/sim_agent.sh run
KillMode=mixed
TimeoutStopSec=40
Restart=on-failure
RestartSec=5
Environment=WEBOTS_HOME=${WEBOTS_HOME:-$HOME/opt/webots}

[Install]
WantedBy=graphical-session.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT"
  echo "installed $UNIT_FILE"
  echo "the simulation now starts with 'docker compose up -d' and stops after 'docker compose down'"
}

uninstall() {
  systemctl --user disable --now "$UNIT" 2>/dev/null
  rm -f "$UNIT_FILE"
  systemctl --user daemon-reload
  echo "removed $UNIT"
}

case "${1:-run}" in
  run) run ;;
  install) install ;;
  uninstall) uninstall ;;
  status) systemctl --user --no-pager status "$UNIT" | head -12; echo; journalctl --user -u "$UNIT" -n 5 --no-pager -o cat | grep sim-agent ;;
  logs) journalctl --user -u "$UNIT" -f -o cat ;;
  *) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
