"""Image gallery / file browser."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from rapidboxes.growth_viz import ensure_growth_image
from rapidboxes.plant_mask import (
    PLANT_MASK_PNG,
    PLANT_OVERLAY_JPG,
    read_plant_mask_progress,
    start_plant_mask_job,
)

from .deps import AppState, get_state

router = APIRouter(prefix="/api/images", tags=["images"])

_ARTIFACT_MEDIA = {
    "plant_mask": ("image/png", PLANT_MASK_PNG),
    "plant_overlay": ("image/jpeg", PLANT_OVERLAY_JPG),
}


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


@router.get("/{experiment_id}", response_model=dict)
async def list_experiment(experiment_id: str, state: AppState = Depends(get_state)):
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    return {"experimentId": exp.experiment_id, "images": exp.list_images()}


@router.get("/{experiment_id}/growth")
async def growth_image(experiment_id: str, state: AppState = Depends(get_state)):
    """Time-colored growth heatmap for an experiment (cached on disk)."""
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    images = exp.list_capture_images()
    if len(images) < 2:
        raise HTTPException(400, "need at least 2 images for growth visualization")
    try:
        path = ensure_growth_image(exp.path, [img["id"] for img in images])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"growth visualization failed: {exc}") from exc
    return FileResponse(path, media_type="image/jpeg")


@router.post("/{experiment_id}/plant-mask/start")
async def plant_mask_start(experiment_id: str, state: AppState = Depends(get_state)):
    """Start (or reuse cached) full-resolution plant-shape extraction."""
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    images = exp.list_capture_images()
    if len(images) < 2:
        raise HTTPException(400, "need at least 2 images for plant mask")
    try:
        return start_plant_mask_job(exp.path, [img["id"] for img in images])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{experiment_id}/plant-mask/status")
async def plant_mask_status(experiment_id: str, state: AppState = Depends(get_state)):
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    return read_plant_mask_progress(exp.path)


@router.get("/{experiment_id}/artifacts/{artifact_id}")
async def artifact_file(experiment_id: str, artifact_id: str, state: AppState = Depends(get_state)):
    if artifact_id not in _ARTIFACT_MEDIA:
        raise HTTPException(404, "unknown artifact")
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    f = exp.artifact_file(artifact_id)
    if f is None:
        raise HTTPException(404, "artifact not found")
    media, _ = _ARTIFACT_MEDIA[artifact_id]
    return FileResponse(f, media_type=media)


@router.get("/{experiment_id}/artifacts/{artifact_id}/thumb")
async def artifact_thumb(experiment_id: str, artifact_id: str, state: AppState = Depends(get_state)):
    if artifact_id not in _ARTIFACT_MEDIA:
        raise HTTPException(404, "unknown artifact")
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    f = exp.artifact_thumb(artifact_id)
    if f is None:
        raise HTTPException(404, "artifact not found")
    return FileResponse(f, media_type="image/jpeg")


@router.get("/{experiment_id}/{image_id}")
async def image_file(experiment_id: str, image_id: str, state: AppState = Depends(get_state)):
    exp = state.storage.get_experiment(experiment_id)
    f = exp.image_file(image_id) if exp else None
    if f is None:
        raise HTTPException(404, "image not found")
    return FileResponse(f, media_type="image/png")


@router.get("/{experiment_id}/{image_id}/thumb")
async def image_thumb(experiment_id: str, image_id: str, state: AppState = Depends(get_state)):
    exp = state.storage.get_experiment(experiment_id)
    f = exp.thumb_file(image_id) if exp else None
    if f is None:
        raise HTTPException(404, "image not found")
    return FileResponse(f, media_type="image/jpeg")
