"""ARCADIA Orchestrator - FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chaos, farms, fleet, incidents, missions, telemetry, world
from app.bridge.manager import bridge
from app.core.config import settings
from app.services import fleet as fleet_svc
from app.services.world_engine import world as world_engine
from app.core.logging import configure_logging

configure_logging()
log = logging.getLogger("arcadia")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s (bridge_mode=%s)", settings.app_name, settings.bridge_mode)
    await bridge.start()
    world_engine.start()
    try:
        await world_engine.load(await fleet_svc.get_active_farm_id())
    except Exception:  # noqa: BLE001 - never block startup on the world model
        log.exception("world model failed to load")
    try:
        yield
    finally:
        await world_engine.stop()
        await bridge.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["*"], allow_headers=["*"])

for r in (farms.router, world.router, fleet.router, missions.router, incidents.router, chaos.router, telemetry.router):
    app.include_router(r, prefix="/api")


# Live telemetry socket lives at the root (Caddy routes /ws/* here), not under the /api prefix.
app.add_api_websocket_route("/ws/telemetry", telemetry.ws_telemetry)


@app.get("/api/health")
async def health():
    return {"ok": True, "bridge": bridge.mode}
