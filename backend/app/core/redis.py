"""Redis helpers: async client for the API, sync client for Celery, channel names."""
from __future__ import annotations

import redis as redis_sync
import redis.asyncio as redis_async

from app.core.config import settings

CH_TELEMETRY = "arcadia:telemetry"      # sim -> dashboards (high-rate, throttled)
CH_EVENTS = "arcadia:events"            # sim -> backend domain events
CH_COMMANDS = "arcadia:commands"        # backend/celery -> sim (dispatch, chaos, world load)
CH_DASHBOARD = "arcadia:dashboard"      # backend -> dashboards (fleet, tasks, incidents, energy)
KEY_FLEET = "arcadia:fleet"             # hash robot_id -> json state
KEY_ACTIVE_FARM = "arcadia:active_farm"
KEY_DISPATCH_PAUSED = "arcadia:dispatch_paused"
KEY_CHAOS_CONFIG = "arcadia:chaos_config"
KEY_BRIDGE_STATUS = "arcadia:bridge_status"
KEY_ENERGY = "arcadia:energy"           # hash infra_id -> json

_async_client: redis_async.Redis | None = None
_sync_client: redis_sync.Redis | None = None


def get_redis() -> redis_async.Redis:
    global _async_client
    if _async_client is None:
        _async_client = redis_async.from_url(settings.redis_url, decode_responses=True)
    return _async_client


def get_sync_redis() -> redis_sync.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis_sync.from_url(settings.redis_url, decode_responses=True)
    return _sync_client
