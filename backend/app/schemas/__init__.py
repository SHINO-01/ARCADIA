"""Pydantic schemas (API I/O + the sim <-> backend wire protocol)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ----------------------------------------------------------------------------- wire protocol
FaultKind = Literal["motor_jam", "battery_drop", "network_latency", "sensor_glitch", "stuck_terrain"]
MissionKind = Literal["survey", "seed", "weed", "harvest", "tow", "charge", "goto", "patrol"]
RobotStatus = Literal["idle", "active", "charging", "fault", "stranded", "towing", "towed", "offline", "returning"]


class Waypoint(BaseModel):
    x: float
    y: float
    z: float = 0.0
    lat: float | None = None
    lng: float | None = None


class TelemetryFrame(BaseModel):
    """One throttled telemetry sample from a robot (ROS arcadia_msgs/Telemetry mirrors this)."""
    robot_id: str
    type: str | None = None
    status: str = "idle"
    x: float
    y: float
    z: float = 0.0
    heading: float = 0.0
    speed: float = 0.0
    battery_pct: float = 100.0
    battery_v: float = 48.0
    motor_temp_c: float = 35.0
    cpu_load: float = 0.2
    mission_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    stamp: float | None = None
    latency_ms: float = 0.0


class SimEvent(BaseModel):
    """Domain event emitted by the simulation (Webots supervisor/robots or the headless twin)."""
    kind: str
    robot_id: str | None = None
    section: str | None = None   # bed section the event concerns, if any
    mission_id: str | None = None
    severity: Literal["INFO", "WARN", "ERROR"] = "INFO"
    payload: dict[str, Any] = Field(default_factory=dict)
    stamp: float | None = None


class Command(BaseModel):
    """Backend -> simulation command (ROS arcadia_msgs/Command mirrors this)."""
    kind: Literal["mission", "cancel", "return_home", "chaos", "clear_fault", "world_load",
                  "set_active", "tow", "pause", "resume", "set_speed", "set_view", "set_irradiance"]
    robot_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    command_id: str | None = None


class DiagnosticStatus(BaseModel):
    robot_id: str
    level: Literal["OK", "WARN", "ERROR", "STALE"] = "OK"
    items: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


# ----------------------------------------------------------------------------- API models
class FarmCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    boundary: dict  # GeoJSON Feature / Polygon / FeatureCollection
    seed: int = 42


class PlanRequest(BaseModel):
    """Controlled-traffic layout + infrastructure parameters (metres)."""
    k_docks: int | None = Field(default=None, ge=1, le=12)
    k_storage: int | None = Field(default=None, ge=1, le=4)
    seed: int | None = None
    bed_width: float = Field(default=6.0, ge=1.5, le=24.0, description="cropped bed width")
    lane_width: float = Field(default=2.4, ge=1.6, le=6.0, description="tramline width between beds")
    headland_width: float = Field(default=10.0, ge=5.0, le=30.0, description="turning track inside the boundary")
    section_length: float = Field(default=40.0, ge=10.0, le=200.0, description="bed section (work unit) length")
    alley_spacing: float = Field(default=0.0, ge=0.0, le=1000.0, description="cross alleys every N m (0 = auto)")
    orientation: Literal["auto", "ew", "ns"] = "auto"
    shuffle_crops: bool = False


class RoutePoint(BaseModel):
    """A map click: geographic (lat/lng) or local ENU (x/y) - at least one pair is required."""
    lat: float | None = None
    lng: float | None = None
    x: float | None = None
    y: float | None = None

    @model_validator(mode="after")
    def _one_pair(self):
        if (self.lat is None or self.lng is None) and (self.x is None or self.y is None):
            raise ValueError("each waypoint needs lat/lng or x/y")
        return self


class RouteRequest(BaseModel):
    """Operator-built route (RTS style): ordered waypoints, optionally looped as a patrol."""
    waypoints: list[RoutePoint] = Field(min_length=1, max_length=64)
    loop: bool = False
    kind: Literal["patrol", "goto", "survey"] = "patrol"
    note: str | None = None


class FarmOut(BaseModel):
    id: str
    name: str
    boundary: dict
    centroid_lat: float
    centroid_lng: float
    area_m2: float
    status: str
    plan: dict | None
    seed: int
    created_at: datetime
    model_config = {"from_attributes": True}


class InfrastructureOut(BaseModel):
    id: str
    slug: str
    kind: str  # solar_dock | wind_dock | storage | repair_station
    lat: float
    lng: float
    x: float
    y: float
    cluster_size: int
    capacity_kw: float
    storage_kwh: float
    stored_kwh: float
    model_config = {"from_attributes": True}


class RobotOut(BaseModel):
    id: str
    type: str
    name: str
    status: str
    active: bool
    battery_pct: float
    lat: float | None
    lng: float | None
    x: float
    y: float
    z: float
    heading: float
    home_dock: str | None
    current_mission_id: str | None
    fault: dict | None
    diagnostics: dict | None
    last_seen: datetime | None
    model_config = {"from_attributes": True}


class DispatchRequest(BaseModel):
    kind: MissionKind = "goto"
    target_section: str | None = None
    lat: float | None = None
    lng: float | None = None
    x: float | None = None
    y: float | None = None
    priority: int = 1
    note: str | None = None


class ChaosInject(BaseModel):
    robot_id: str | None = None
    kind: FaultKind | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ChaosConfig(BaseModel):
    enabled: bool = True
    interval_s: float = Field(default=90.0, ge=5, le=3600)
    probability: float = Field(default=0.6, ge=0, le=1)
    kinds: list[FaultKind] = ["motor_jam", "battery_drop", "network_latency", "sensor_glitch", "stuck_terrain"]


class MissionOut(BaseModel):
    id: str
    farm_id: str | None
    robot_id: str | None
    kind: str
    status: str
    priority: int
    source: str
    target_section: str | None
    waypoints: list | None
    payload: dict | None
    result: dict | None
    created_at: datetime
    dispatched_at: datetime | None
    completed_at: datetime | None
    note: str | None
    model_config = {"from_attributes": True}


class IncidentOut(BaseModel):
    id: str
    farm_id: str | None
    robot_id: str
    kind: str
    severity: str
    state: str
    title: str
    detail: dict | None
    recovery_mission_id: str | None
    requires_tow: bool
    created_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    model_config = {"from_attributes": True}
