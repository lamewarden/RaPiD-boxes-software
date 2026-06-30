"""FastAPI application: serves the API, WebSocket, MJPEG preview, and the SPA.

One process does everything (see plan: "single Python process + kiosk"). The
React build (dist/spa) is served at "/" with a catch-all so client-side routes
work; everything under /api is the backend.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig, get_config
from .engine.runner import ExperimentRunner
from .hardware.manager import build_hardware
from .settings_store import load_device_settings_for_new_session
from .storage import Storage
from .api import experiments, images, preview, settings as settings_api, system, ws
from .api.deps import AppState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rapidboxes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config: AppConfig = app.state._config
    device_settings = load_device_settings_for_new_session(config.settings_path)
    storage = Storage(config.storage_root)
    hw = build_hardware(config, device_settings)
    runner = ExperimentRunner(hw, storage)
    runner.recover()
    app.state.app = AppState(config, device_settings, storage, hw, runner)
    log.info("RaPiD-boxes started (simulation=%s, storage=%s)", config.simulation, config.storage_root)
    try:
        yield
    finally:
        await runner.shutdown()
        log.info("RaPiD-boxes stopped; hardware released")


def create_app(config: Optional[AppConfig] = None) -> FastAPI:
    config = config or get_config()
    app = FastAPI(title="RaPiD-boxes", version="0.1.0", lifespan=lifespan)
    app.state._config = config

    # Dev convenience: the Vite dev server (other origin) can call the API directly.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (experiments, images, settings_api, system, preview):
        app.include_router(module.router)
    app.include_router(ws.router)

    _mount_spa(app, config)
    return app


def _mount_spa(app: FastAPI, config: AppConfig) -> None:
    spa = config.spa_dir
    if not spa or not spa.exists():
        log.info("no SPA bundle mounted (spa_dir unset); use the Vite dev server in development")
        return

    assets = spa / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = spa / "index.html"

    @app.get("/{full_path:path}")
    async def spa_catch_all(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(404, "API endpoint not found")
        candidate = spa / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # index.html picks which hashed JS/CSS bundle loads; never let the
        # browser cache it, or a kiosk relaunch can silently keep showing a
        # stale build even right after a fresh deploy.
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    log.info("serving SPA from %s", spa)


app = create_app()
