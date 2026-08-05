"""OTA self-update endpoints for the Settings -> General "Update" button.

Both endpoints only ever fetch + fast-forward-merge (see ../updater.py); they
never reset or discard local changes. Applying an update does not restart the
service itself -- the frontend shows a "restart now?" dialog after a
successful apply and, on confirm, calls the existing POST
/api/system/restart-service endpoint (see api/system.py).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ..models import UpdateApplyResult, UpdateCheckResult
from ..updater import apply_update, check_for_update
from .deps import AppState, get_state

router = APIRouter(prefix="/api/system/update", tags=["update"])


@router.get("/check", response_model=UpdateCheckResult)
async def check(state: AppState = Depends(get_state)):
    """git fetch + compare HEAD to origin/<update_branch>. Read-only."""
    # git fetch does network I/O; run off the event loop so it can't stall
    # the WS status feed / MJPEG preview during an active experiment.
    return await asyncio.to_thread(check_for_update, state.config.update_branch)


@router.post("/apply", response_model=UpdateApplyResult)
async def apply(state: AppState = Depends(get_state)):
    """git fetch + `merge --ff-only`. Refuses (no-op) if it can't fast-forward."""
    return await asyncio.to_thread(apply_update, state.config.update_branch)
