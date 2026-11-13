#!/usr/bin/env sh
set -e
case "$1" in
  api)
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*" --ws-ping-interval 20 --timeout-graceful-shutdown 5
    ;;
  worker)
    exec celery -A app.workers.celery_app:celery_app worker -Q dispatch,chaos,telemetry -c "${CELERY_CONCURRENCY:-2}" -l INFO
    ;;
  beat)
    exec celery -A app.workers.celery_app:celery_app beat -l INFO
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
