"""Experiment lifecycle + history."""
from __future__ import annotations

import shutil
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from .. import config_xml
from ..models import (
    ExperimentConfig,
    ExperimentStatus,
    FreeSpaceRequest,
    FreeSpaceResponse,
    SavedExperimentConfig,
    StartResponse,
)
from ..storage import ExperimentDir
from .deps import AppState, get_state

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


@router.post("", response_model=StartResponse)
async def start_experiment(config: ExperimentConfig, state: AppState = Depends(get_state)):
    return await state.runner.start(config, state.settings.camera)


@router.post("/free-space", response_model=FreeSpaceResponse)
async def free_space(body: FreeSpaceRequest, state: AppState = Depends(get_state)):
    """Delete experiment folders to make room for a "low_space" start retry.

    Ownership is re-checked server-side against each folder's own username
    (never trusts the client's id list), and the active run -- if any -- is
    never a candidate, so this can only ever remove the requesting user's own,
    not-currently-running experiments."""
    requested = set(body.experimentIds)
    active_id = (
        state.runner.status.experimentId
        if state.runner.status.state in ("running", "paused", "finishing")
        else None
    )
    want_user = body.username.strip().lower()

    deleted: List[str] = []
    freed = 0
    for d in state.storage.list_experiments():
        exp = ExperimentDir(d)
        if exp.experiment_id not in requested or exp.experiment_id == active_id:
            continue
        owner = exp.username()
        if not owner or owner.strip().lower() != want_user:
            continue
        size = exp.size_bytes()
        if state.storage.delete_experiment(exp.experiment_id):
            deleted.append(exp.experiment_id)
            freed += size

    available = shutil.disk_usage(state.storage.root).free
    return FreeSpaceResponse(deletedIds=deleted, freedBytes=freed, availableBytes=available)


@router.get("/current", response_model=ExperimentStatus)
async def current(state: AppState = Depends(get_state)):
    return state.runner.status


@router.post("/current/pause", response_model=ExperimentStatus)
async def pause(state: AppState = Depends(get_state)):
    await state.runner.pause()
    return state.runner.status


@router.post("/current/resume", response_model=ExperimentStatus)
async def resume(state: AppState = Depends(get_state)):
    await state.runner.resume()
    return state.runner.status


@router.post("/current/stop", response_model=ExperimentStatus)
async def stop(state: AppState = Depends(get_state)):
    await state.runner.stop()
    return state.runner.status


@router.post("/current/abort", response_model=ExperimentStatus)
async def abort(state: AppState = Depends(get_state)):
    """Stop the running experiment and delete its folder/images."""
    await state.runner.abort()
    return state.runner.status


@router.get("/history")
async def history(state: AppState = Depends(get_state)) -> List[dict]:
    out = []
    for d in state.storage.list_experiments():
        exp = ExperimentDir(d)
        meta = exp.read_metadata() or {}
        out.append(
            {
                "id": exp.experiment_id,
                "name": meta.get("experimentName"),
                "username": exp.username(),
                "startedAt": meta.get("startedAt"),
                "state": meta.get("state"),
                "imagesCaptured": meta.get(
                    "imagesCaptured", len(exp.list_capture_images())
                ),
            }
        )
    return out


@router.get("/{experiment_id}/config", response_model=SavedExperimentConfig)
async def get_config(experiment_id: str, state: AppState = Depends(get_state)):
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    data = exp.read_config_xml()
    if data is None:
        raise HTTPException(404, "no saved config for this experiment")
    try:
        return config_xml.parse(data)
    except Exception:
        raise HTTPException(500, "could not parse saved config")
