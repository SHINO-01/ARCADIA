"""Central configuration for the ARCADIA backend (pydantic-settings, env-driven)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARCADIA_", env_file=".env", extra="ignore")

    app_name: str = "ARCADIA Orchestrator"
    env: Literal["dev", "prod", "test"] = "dev"
    log_level: str = "INFO"

    # Persistence
    database_url: str = "postgresql+asyncpg://arcadia:arcadia@localhost:55432/arcadia"
    sync_database_url: str | None = None  # derived from database_url when None
    redis_url: str = "redis://localhost:6379/0"

    # ROS bridge. "auto" tries rosbridge first and falls back to the headless twin.
    rosbridge_url: str = "ws://host.docker.internal:9090"
    bridge_mode: Literal["auto", "ros", "headless"] = "auto"
    bridge_reconnect_s: float = 3.0
    headless_tick_hz: float = 10.0

    # Telemetry
    telemetry_persist_batch: int = 50
    telemetry_persist_interval_s: float = 2.0
    telemetry_retention_days: int = 14

    # Spatial engine

    # Fleet (hardware constraint: max 2 active robots on the IdeaPad)
    max_active_robots: int = 2
    default_robots: str = "scout_uav,seeder_ugv"

    # Chaos engine
    chaos_enabled: bool = True
    chaos_interval_s: float = 90.0
    chaos_probability: float = 0.6

    # Dispatch
    auto_dispatch: bool = True
    auto_dispatch_interval_s: float = 5.0
    energy_tick_s: float = 2.0

    # Celery
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    cors_origins: str = "*"

    @property
    def broker(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def sync_db_url(self) -> str:
        if self.sync_database_url:
            return self.sync_database_url
        return self.database_url.replace("+asyncpg", "+psycopg")

    @property
    def robot_roster(self) -> list[str]:
        return [r.strip() for r in self.default_robots.split(",") if r.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
