#!/usr/bin/env bash
# Run the backend natively (outside Docker) against the compose Postgres/Redis for fast iteration.
# usage: scripts/dev_backend.sh start|stop|restart|status|logs
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="${ARCADIA_RUN_DIR:-$ROOT/.run}"
mkdir -p "$RUN"
export ARCADIA_DATABASE_URL="${ARCADIA_DATABASE_URL:-postgresql+asyncpg://arcadia:arcadia@localhost:55432/arcadia}"
export ARCADIA_REDIS_URL="${ARCADIA_REDIS_URL:-redis://localhost:6379/0}"
export ARCADIA_ROSBRIDGE_URL="${ARCADIA_ROSBRIDGE_URL:-ws://localhost:9090}"
PY="$ROOT/backend/.venv/bin"

start_one() { # name, cmd...
  local name="$1"; shift
  if [[ -f "$RUN/$name.pid" ]] && kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null; then echo "$name already running"; return; fi
  (cd "$ROOT/backend" && nohup "$@" > "$RUN/$name.log" 2>&1 & echo $! > "$RUN/$name.pid")
  echo "started $name (pid $(cat "$RUN/$name.pid"))"
}
stop_one() {
  local name="$1"
  if [[ -f "$RUN/$name.pid" ]]; then
    local pid; pid="$(cat "$RUN/$name.pid")"
    kill "$pid" 2>/dev/null && echo "stopped $name" || true
    pkill -P "$pid" 2>/dev/null || true
    rm -f "$RUN/$name.pid"
  fi
}
case "${1:-start}" in
  start)
    (cd "$ROOT/backend" && "$PY/alembic" upgrade head >/dev/null)
    start_one api "$PY/uvicorn" app.main:app --port "${ARCADIA_API_PORT:-8000}" --ws-ping-interval 20
    start_one worker "$PY/celery" -A app.workers.celery_app:celery_app worker -Q dispatch,chaos,telemetry -c 2 -l INFO
    start_one beat "$PY/celery" -A app.workers.celery_app:celery_app beat -l INFO ;;
  stop) for n in beat worker api; do stop_one "$n"; done ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
  status) for n in api worker beat; do if [[ -f "$RUN/$n.pid" ]] && kill -0 "$(cat "$RUN/$n.pid")" 2>/dev/null; then echo "$n: running"; else echo "$n: stopped"; fi; done ;;
  logs) tail -n 50 -f "$RUN"/*.log ;;
  *) echo "usage: $0 start|stop|restart|status|logs"; exit 1 ;;
esac
