"""Experiment lifecycle + history."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException

from .. import config_xml
from ..models import ExperimentStatus, SavedExperimentConfig, StartResponse, TropismConfig
from .deps import AppState, get_state

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


@router.post("", response_model=StartResponse)
async def start_experiment(config: TropismConfig, state: AppState = Depends(get_state)):
    return await state.runner.start(config, state.settings.camera)


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


@router.get("/history")
async def history(state: AppState = Depends(get_state)) -> List[dict]:
    out = []
    for d in state.storage.list_experiments():
        from ..storage import ExperimentDir

        exp = ExperimentDir(d)
        meta = exp.read_metadata() or {}
        out.append(
            {
                "id": exp.experiment_id,
                "name": meta.get("experimentName"),
                "username": meta.get("username"),
                "startedAt": meta.get("startedAt"),
                "state": meta.get("state"),
                "imagesCaptured": meta.get("imagesCaptured", len(exp.list_images())),
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
