"""SQLAlchemy ORM entities for ARCADIA.

Telemetry and EnergyLog are time-series tables; when the TimescaleDB extension is
available they are converted to hypertables (see alembic migration 0001).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


class Farm(Base):
    __tablename__ = "farms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    boundary: Mapped[dict] = mapped_column(JSONType, nullable=False)  # GeoJSON Polygon
    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lng: Mapped[float] = mapped_column(Float, nullable=False)
    area_m2: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="mapped")  # mapped|planned|active|archived
    plan: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # k-means report
    world_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # payload for Webots spawner
    setup: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # site analysis: imagery, climate, soil
    crop_plan: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # {crop, rotation?, sections: {id: crop}}
    seed: Mapped[int] = mapped_column(Integer, default=42)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    infrastructure: Mapped[list[Infrastructure]] = relationship(
        back_populates="farm", cascade="all, delete-orphan"
    )


class FieldSection(Base):
    """A bed section: the agronomic management unit (state, observation, work)."""
    __tablename__ = "sections"

    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(16), primary_key=True)   # e.g. "AB-03"
    bed_id: Mapped[str] = mapped_column(String(16), index=True)
    crop: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(16), default="fallow")  # fallow|seeded|growing|ripe|harvested
    weeds: Mapped[bool] = mapped_column(Boolean, default=False)
    moisture: Mapped[float] = mapped_column(Float, default=0.5)
    health: Mapped[float] = mapped_column(Float, default=1.0)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    length: Mapped[float] = mapped_column(Float)
    width: Mapped[float] = mapped_column(Float)
    angle: Mapped[float] = mapped_column(Float)
    polygon: Mapped[list] = mapped_column(JSONType)   # ENU ring
    passes: Mapped[list] = mapped_column(JSONType)    # tramline work passes
    last_survey_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # latest UAV measurements (world-model sensor model, with noise)
    ndvi: Mapped[float | None] = mapped_column(Float, nullable=True)
    cwsi: Mapped[float | None] = mapped_column(Float, nullable=True)
    disease: Mapped[float | None] = mapped_column(Float, nullable=True)
    weed_cover: Mapped[float | None] = mapped_column(Float, nullable=True)
    yield_est: Mapped[float | None] = mapped_column(Float, nullable=True)   # t/ha fresh
    soil: Mapped[dict | None] = mapped_column(JSONType, nullable=True)      # clay, sand, om, ec, taw, twi ...
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorldState(Base):
    """Agronomic world-model snapshot per farm and timeline (resumes the world after a restart).

    `mode` separates the real-time twin ("realtime", clock = wall clock) from the what-if
    sandbox ("simulation", accelerated clock, forked from the real-time state)."""
    __tablename__ = "world_states"

    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), primary_key=True, default="realtime")
    sim_date: Mapped[str] = mapped_column(String(10))          # ISO date
    sim_day_frac: Mapped[float] = mapped_column(Float, default=0.25)
    day_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(Text)                   # base64 npz of the field state
    weather: Mapped[dict] = mapped_column(JSONType)            # generator state
    extra: Mapped[dict] = mapped_column(JSONType)              # scenarios, operations, observations, detector
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WeatherDaily(Base):
    __tablename__ = "weather_daily"

    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), primary_key=True, default="realtime")
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONType)


class FarmDaily(Base):
    __tablename__ = "farm_daily"

    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), primary_key=True, default="realtime")
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONType)


class Anomaly(Base):
    __tablename__ = "anomalies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(10), default="realtime")
    key: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSONType)               # detail, method, value, threshold, action, sections
    started: Mapped[str] = mapped_column(String(10))
    ended: Mapped[str | None] = mapped_column(String(10), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Infrastructure(Base):
    __tablename__ = "infrastructure"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(32), nullable=False)  # dock-1, storage-1
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # solar_dock|wind_dock|storage|repair_station
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    cluster_size: Mapped[int] = mapped_column(Integer, default=0)
    capacity_kw: Mapped[float] = mapped_column(Float, default=12.0)
    storage_kwh: Mapped[float] = mapped_column(Float, default=60.0)
    stored_kwh: Mapped[float] = mapped_column(Float, default=40.0)
    meta: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    farm: Mapped[Farm] = relationship(back_populates="infrastructure")


class Robot(Base):
    __tablename__ = "robots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uav-1, ugv-1, agv-1
    farm_id: Mapped[str | None] = mapped_column(ForeignKey("farms.id", ondelete="SET NULL"), nullable=True)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="idle")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    battery_pct: Mapped[float] = mapped_column(Float, default=100.0)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    z: Mapped[float] = mapped_column(Float, default=0.0)
    heading: Mapped[float] = mapped_column(Float, default=0.0)
    home_dock: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_mission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    fault: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    odometer_m: Mapped[float] = mapped_column(Float, default=0.0)


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = (Index("ix_telemetry_robot_time", "robot_id", "time"),)

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    robot_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    z: Mapped[float] = mapped_column(Float)
    heading: Mapped[float] = mapped_column(Float)
    speed: Mapped[float] = mapped_column(Float)
    battery_pct: Mapped[float] = mapped_column(Float)
    battery_v: Mapped[float] = mapped_column(Float)
    motor_temp_c: Mapped[float] = mapped_column(Float)
    cpu_load: Mapped[float] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(16))


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    farm_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    robot_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # motor_jam|battery_drop|network_latency
    severity: Mapped[str] = mapped_column(String(8), default="ERROR")  # WARN|ERROR
    state: Mapped[str] = mapped_column(String(16), default="open")  # open|acknowledged|recovering|resolved
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    recovery_mission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    requires_tow: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    farm_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    robot_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # survey|seed|weed|harvest|tow|charge|goto
    status: Mapped[str] = mapped_column(String(16), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=5)
    source: Mapped[str] = mapped_column(String(16), default="auto")  # auto|operator|recovery
    target_section: Mapped[str | None] = mapped_column(String(16), nullable=True)
    waypoints: Mapped[list | None] = mapped_column(JSONType, nullable=True)  # [{x,y,z,lat,lng}]
    payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class YieldRecord(Base):
    __tablename__ = "yields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[str] = mapped_column(String(36), index=True)
    mode: Mapped[str] = mapped_column(String(10), default="realtime")
    section_id: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    crop_type: Mapped[str] = mapped_column(String(24))
    kg: Mapped[float] = mapped_column(Float)
    robot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    harvested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EnergyLog(Base):
    __tablename__ = "energy_log"
    __table_args__ = (Index("ix_energy_infra_time", "infrastructure_id", "time"),)

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    infrastructure_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generated_kw: Mapped[float] = mapped_column(Float)
    consumed_kw: Mapped[float] = mapped_column(Float)
    stored_kwh: Mapped[float] = mapped_column(Float)
    irradiance: Mapped[float] = mapped_column(Float)
    wind_ms: Mapped[float] = mapped_column(Float)
