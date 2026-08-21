"""OTA self-update endpoints for the Settings -> General "Update"/"Version" cards.

check/apply only ever fetch + fast-forward-merge; rollback only ever
`git checkout --detach` to a previously-recorded commit (see ../updater.py
for why that's used instead of `git reset --hard`). None of them restart the
service itself -- the frontend shows a "restart now?" dialog after a
successful apply/rollback and, on confirm, calls the existing POST
/api/system/restart-service endpoint (see api/system.py).
"""
from __future__ import annotations

import asyncio
from functools import partial

from fastapi import APIRouter, Depends

from ..models import UpdateApplyResult, UpdateCheckResult, VersionStatus
from ..updater import BUSY_EXPERIMENT_STATES, apply_update, check_for_update, get_version_status, rollback_update
from .deps import AppState, get_state

router = APIRouter(prefix="/api/system/update", tags=["update"])


@router.get("/check", response_model=UpdateCheckResult)
async def check(state: AppState = Depends(get_state)):
    """git fetch + compare HEAD to origin/<update_branch>. Read-only.

    Always allowed, even mid-experiment -- it never touches the working tree.
    """
    # git fetch does network I/O; run off the event loop so it can't stall
    # the WS status feed / MJPEG preview during an active experiment.
    return await asyncio.to_thread(check_for_update, state.config.update_branch)


@router.get("/version", response_model=VersionStatus)
async def version(state: AppState = Depends(get_state)):
    """Currently-running commit + how long, and the previous one (if any).

    Read-only (may lazily write a single "seed" history entry the very first
    time it's called on a box with no history yet -- see
    updater.get_version_status) -- always allowed, even mid-experiment.
    """
    return await asyncio.to_thread(get_version_status, state.config.update_history_path)


@router.post("/apply", response_model=UpdateApplyResult)
async def apply(state: AppState = Depends(get_state)):
    """git fetch + `merge --ff-only`. Refuses (no-op) if it can't fast-forward.

    Also refuses outright while an experiment is running/paused/finishing --
    this process is in-process with the live ExperimentRunner (unlike the
    CLI/timer path, which has to ask over loopback HTTP; see
    updater.check_experiment_active_via_http), so we can just read
    state.runner.status.state directly.
    """
    if state.runner.status.state in BUSY_EXPERIMENT_STATES:
        return UpdateApplyResult(
            status="experiment_active",
            message="Finish or stop the current experiment before updating.",
        )
    return await asyncio.to_thread(
        partial(
            apply_update,
            state.config.update_branch,
            history_path=state.config.update_history_path,
            trigger="manual",
        )
    )


@router.post("/rollback", response_model=UpdateApplyResult)
async def rollback(state: AppState = Depends(get_state)):
    """Move the working tree back to the previously-recorded commit.

    Same busy-experiment gate as apply(); see updater.rollback_update() for
    why this uses `git checkout --detach` rather than `git reset --hard`, and
    why the frontend surfaces a warning that a monthly auto-update tracking
    the same branch can fast-forward straight back onto a rolled-back commit.
    """
    if state.runner.status.state in BUSY_EXPERIMENT_STATES:
        return UpdateApplyResult(
            status="experiment_active",
            message="Finish or stop the current experiment before rolling back.",
        )
    return await asyncio.to_thread(partial(rollback_update, state.config.update_history_path))
