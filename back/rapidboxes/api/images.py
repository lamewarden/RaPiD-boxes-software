"""Image gallery / file browser."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .deps import AppState, get_state

router = APIRouter(prefix="/api/images", tags=["images"])


def _resolve(state: AppState, experiment_id: Optional[str]):
    if experiment_id:
        return state.storage.get_experiment(experiment_id)
    if state.runner.current_experiment is not None:
        return state.runner.current_experiment
    return state.storage.latest_experiment()


@router.get("", response_model=dict)
async def list_current(state: AppState = Depends(get_state)):
    """Images of the running experiment, or the most recent one if idle."""
    exp = _resolve(state, None)
    if exp is None:
        return {"experimentId": None, "images": []}
    return {"experimentId": exp.experiment_id, "images": exp.list_images()}


@router.get("/{experiment_id}", response_model=list)
async def list_experiment(experiment_id: str, state: AppState = Depends(get_state)):
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    return exp.list_images()


@router.get("/{experiment_id}/{image_id}")
async def image_file(experiment_id: str, image_id: str, state: AppState = Depends(get_state)):
    exp = state.storage.get_experiment(experiment_id)
    f = exp.image_file(image_id) if exp else None
    if f is None:
        raise HTTPException(404, "image not found")
    return FileResponse(f, media_type="image/jpeg")


@router.get("/{experiment_id}/{image_id}/thumb")
async def image_thumb(experiment_id: str, image_id: str, state: AppState = Depends(get_state)):
    exp = state.storage.get_experiment(experiment_id)
    f = exp.thumb_file(image_id) if exp else None
    if f is None:
        raise HTTPException(404, "image not found")
    return FileResponse(f, media_type="image/jpeg")
