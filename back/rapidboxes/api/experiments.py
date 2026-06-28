"""Experiment lifecycle + history."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends

from ..models import ExperimentStatus, StartResponse, TropismConfig
from .deps import AppState, get_state

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


@router.post("", response_model=StartResponse)
async def start_experiment(config: TropismConfig, state: AppState = Depends(get_state)):
    return await state.runner.start(config)


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
