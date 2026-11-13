"""ARCADIA Orchestrator - FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chaos, farms, fleet, incidents, missions, telemetry, world
from app.bridge.manager import bridge
from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()
log = logging.getLogger("arcadia")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s (bridge_mode=%s)", settings.app_name, settings.bridge_mode)
    await bridge.start()
    try:
        yield
    finally:
        await bridge.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["*"], allow_headers=["*"])

for r in (farms.router, world.router, fleet.router, missions.router, incidents.router, chaos.router, telemetry.router):
    app.include_router(r, prefix="/api")


@app.get("/api/health")
async def health():
    return {"ok": True, "bridge": bridge.mode}
