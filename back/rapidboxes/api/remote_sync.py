"""Remote CIFS sync configuration + actions (Settings -> General -> Remote Sync).

Deliberately a separate router from /api/settings: the remote-sync config is
not a "how the image was taken" device setting (it must not travel into the
per-experiment config XML), and separating it keeps the password well away
from the DeviceSettings object that GET /api/settings serialises wholesale.

The password is accepted here on PUT and nowhere else. No route in this file --
or any other -- ever returns it: RemoteSyncStatus has no field for it, only
`passwordSet`.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..models import (
    RemoteSyncStatus,
    RemoteSyncUpdate,
    validate_remote_server,
    validate_remote_username,
)
from .deps import AppState, get_state

router = APIRouter(prefix="/api/settings/remote-sync", tags=["remote-sync"])


class CheckConnectionResult(BaseModel):
    ok: bool
    message: str
    status: RemoteSyncStatus


class SyncAllRequest(BaseModel):
    # The researcher whose experiments to bulk-copy. Defaults to whoever sync
    # is currently armed for.
    researcher: Optional[str] = None


@router.get("", response_model=RemoteSyncStatus)
async def get_remote_sync(state: AppState = Depends(get_state)):
    return state.sync.status()


@router.put("", response_model=RemoteSyncStatus)
async def put_remote_sync(update: RemoteSyncUpdate, state: AppState = Depends(get_state)):
    """Patch the config. Only fields actually sent are touched.

    `password` is write-only: it goes into process memory and is not echoed
    back, not persisted, and not logged.
    """
    sync = state.sync
    settings = sync.settings

    if update.server is not None:
        try:
            settings.server = validate_remote_server(update.server)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if update.username is not None:
        # An empty username is how the UI clears the field mid-edit; only
        # validate something that is actually being set.
        if update.username.strip():
            try:
                settings.username = validate_remote_username(update.username)
            except ValueError as e:
                raise HTTPException(400, str(e))
        else:
            settings.username = ""
    if update.researcher is not None and update.researcher.strip():
        settings.researcher = update.researcher.strip()
    if update.password is not None:
        sync.set_password(update.password)

    if update.enabled is not None:
        if update.enabled:
            if not settings.username or not sync.password_set:
                raise HTTPException(400, "a username and password are required to switch sync on")
            if not settings.researcher:
                raise HTTPException(400, "no active researcher — set a user name on the home screen first")
            settings.enabled = True
        else:
            settings.enabled = False
            # Drop the session password with the toggle: leaving it in memory
            # after the user has explicitly turned sync off serves no purpose.
            sync.clear_password()
            await sync.unmount()

    sync.persist()
    return sync.status()


@router.post("/check", response_model=CheckConnectionResult)
async def check_connection(state: AppState = Depends(get_state)):
    """Mount (if needed) and prove the destination is actually writable.

    Reports the real error text from mount rather than a generic failure --
    "wrong password" and "host unreachable" need different fixes.
    """
    sync = state.sync
    if not sync.settings.username or not sync.password_set:
        raise HTTPException(400, "a username and password are required")
    ok, message = await sync.check_connection()
    return CheckConnectionResult(ok=ok, message=message, status=sync.status())


@router.post("/sync-all", response_model=RemoteSyncStatus)
async def sync_all(request: SyncAllRequest, state: AppState = Depends(get_state)):
    """One-shot bulk copy of every local experiment belonging to this researcher.

    Returns immediately; the copy runs on the same background worker as
    per-capture syncing, so it can never block an experiment.
    """
    sync = state.sync
    researcher = (request.researcher or sync.settings.researcher or "").strip()
    if not researcher:
        raise HTTPException(400, "no researcher given")
    if not sync.settings.enabled or not sync.password_set:
        raise HTTPException(
            400,
            "remote sync is not active — switch it on and enter the password "
            "(it is not stored and must be re-entered after a restart)",
        )
    sync.enqueue_bulk(researcher)
    return sync.status()
