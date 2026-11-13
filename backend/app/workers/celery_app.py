"""Celery application: automated dispatch queue, chaos engine, recovery protocol, energy model."""
from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery("arcadia", broker=settings.broker, backend=settings.result_backend, include=["app.workers.tasks"])
celery_app.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"], timezone="UTC", enable_utc=True,
    task_acks_late=True, worker_prefetch_multiplier=1, result_expires=3600, broker_connection_retry_on_startup=True,
    task_routes={
        "arcadia.dispatch.*": {"queue": "dispatch"},
        "arcadia.chaos.*": {"queue": "chaos"},
        "arcadia.recovery.*": {"queue": "dispatch"},
        "arcadia.energy.*": {"queue": "telemetry"},
        "arcadia.persist.*": {"queue": "telemetry"},
    },
    task_default_queue="dispatch",
    beat_schedule={
        "auto-dispatch": {"task": "arcadia.dispatch.tick", "schedule": settings.auto_dispatch_interval_s},
        "mission-watchdog": {"task": "arcadia.dispatch.watchdog", "schedule": 30.0},
        "chaos-tick": {"task": "arcadia.chaos.tick", "schedule": 10.0},
        "energy-tick": {"task": "arcadia.energy.tick", "schedule": settings.energy_tick_s},
        "fleet-snapshot": {"task": "arcadia.persist.fleet_snapshot", "schedule": 15.0},
        "telemetry-retention": {"task": "arcadia.persist.retention", "schedule": 3600.0},
    },
)
