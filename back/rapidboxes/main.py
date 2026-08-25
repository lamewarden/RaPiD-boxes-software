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

from .assistant import summary as assistant_summary
from .assistant.mold_watch import MoldWatchService
from .assistant.service import AssistantService
from .config import AppConfig, get_config
from .engine.runner import ExperimentRunner
from .hardware.manager import build_hardware
from .remote_sync import RemoteSyncService, load_remote_sync_settings
from .retention import cleanup_expired_experiments
from .settings_store import load_device_settings_for_new_session
from .storage import Storage
from .telegram_link import TelegramLinkService
from .api import (
    assistant as assistant_api,
    experiments,
    health,
    images,
    preview,
    remote_sync as remote_sync_api,
    settings as settings_api,
    system,
    telegram as telegram_api,
    update,
    users,
    ws,
)
from .api.deps import AppState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rapidboxes")

# httpx logs "HTTP Request: <method> <url> ..." at INFO for every call. That's
# harmless for the LLM gateway (auth is a header), but the Telegram Bot API
# embeds its token directly in the URL path (https://api.telegram.org/bot
# <TOKEN>/method -- there is no header-based alternative in that API), which
# the getUpdates poll hits every few seconds. Left at INFO this writes the
# token into the systemd journal in plaintext, repeatedly, forever. Our own
# code already logs real failures explicitly (e.g. "assistant model call
# failed") at WARNING/ERROR, so silencing httpx's own per-request noise loses
# no error visibility.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config: AppConfig = app.state._config
    device_settings = load_device_settings_for_new_session(config.settings_path)
    storage = Storage(config.storage_root)
    cleanup_expired_experiments(storage)
    hw = build_hardware(config, device_settings)

    # Remote CIFS sync. The persisted half (server/user/on-off) survives a
    # restart; the password deliberately does not, so a box that comes back up
    # with `enabled: true` reports credentialsRequired until a human re-enters
    # it -- see remote_sync.py and the "Remote Sync" card in the UI.
    sync = RemoteSyncService(
        load_remote_sync_settings(config.remote_sync_path),
        storage_root=config.storage_root,
        simulation=config.simulation,
        settings_path=config.remote_sync_path,
    )
    sync.start()
    if sync.credentials_required:
        log.warning(
            "remote sync is switched on but has no password after this restart; "
            "it stays inactive until the password is re-entered in Settings"
        )

    async def on_experiment_finished(exp, status):
        await assistant_summary.generate_and_store(config, exp, status)

    # Opt-in issue-alert delivery + Telegram chat access to PidiBot (see
    # telegram_link.py). Degrades to a no-op if no admin has set a bot
    # token/username yet -- see its own `configured` property.
    telegram = TelegramLinkService(
        config.telegram_bot_token, config.telegram_bot_username, config.telegram_links_path, storage
    )
    telegram.start()

    # Opt-in mid-run mold/anomaly watcher (see assistant/mold_watch.py). Fans
    # out from the same on_image_captured hook remote sync already uses --
    # `runner` isn't constructed yet at this point, but dispatch_image_captured
    # isn't called until real captures happen, well after `runner` below is
    # assigned, so the closure resolving it lazily is safe.
    mold_watch = MoldWatchService(config, storage, telegram)
    mold_watch.start()

    def dispatch_image_captured(path, experiment_id: str, username: str) -> None:
        sync.enqueue_image(path, experiment_id, username)
        live = runner.status.config if runner.status.experimentId == experiment_id else None
        mold_watch.enqueue_image(
            path, experiment_id, username, report_enabled=bool(getattr(live, "reportOnIssueEnabled", False))
        )

    runner = ExperimentRunner(
        hw,
        storage,
        on_image_captured=dispatch_image_captured,
        on_experiment_finished=on_experiment_finished,
    )
    mold_watch.attach_runner(runner)
    await runner.recover()
    # recover() may have overridden the camera/source half of hw's settings to
    # match a resumed experiment's own saved config (see
    # HardwareManager.restore_experiment_settings) -- read it back from hw
    # rather than the pre-recover() `device_settings`, so GET /api/settings
    # and the Camera Settings UI agree with what's actually driving captures.
    assistant = AssistantService(config, storage, runner)
    telegram.attach_assistant(assistant)
    app.state.app = AppState(config, hw.settings, storage, hw, runner, sync, assistant, telegram)
    log.info("RaPiD-boxes started (simulation=%s, storage=%s)", config.simulation, config.storage_root)
    try:
        yield
    finally:
        await runner.shutdown()
        await sync.shutdown()
        await mold_watch.shutdown()
        await telegram.shutdown()
        await assistant.aclose()
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

    for module in (
        assistant_api,
        experiments,
        images,
        settings_api,
        remote_sync_api,
        system,
        telegram_api,
        update,
        users,
        preview,
        health,
    ):
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
