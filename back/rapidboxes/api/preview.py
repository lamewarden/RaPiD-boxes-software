"""Live camera preview: MJPEG stream + single snapshot."""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..hardware.base import CameraUnavailableError
from ..models import CameraSettings, ExperimentState
from .deps import AppState, get_state

log = logging.getLogger("rapidboxes.preview")
router = APIRouter(prefix="/api/preview", tags=["preview"])

_BOUNDARY = "frame"


class LiveBacklightRequest(BaseModel):
    mode: Literal["off", "white", "ir"] = Field(
        description="Live assist light: off, RGBW fill (10,10,10,10), or IR boards high"
    )


class LiveBacklightResponse(BaseModel):
    mode: Literal["off", "white", "ir"]


def _busy_if_experiment(state: AppState) -> None:
    if state.runner.status.state in (
        ExperimentState.running,
        ExperimentState.paused,
        ExperimentState.finishing,
    ):
        raise HTTPException(409, "an experiment is running")


async def _mjpeg(state: AppState):
    period = 1.0 / max(0.5, state.config.preview_fps)
    try:
        while True:
            frame = await state.hw.preview_frame()
            yield (
                b"--" + _BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )
            await asyncio.sleep(period)
    except asyncio.CancelledError:  # client disconnected
        raise
    except Exception:
        log.exception("preview stream error")
    finally:
        # Live closed / stream dropped — kill any assist backlight we armed.
        try:
            await state.hw.clear_live_backlight()
        except Exception:
            log.exception("clear_live_backlight after preview failed")


@router.get("")
async def preview_stream(state: AppState = Depends(get_state)):
    return StreamingResponse(
        _mjpeg(state),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
    )


@router.post("/backlight", response_model=LiveBacklightResponse)
async def set_live_backlight(
    body: LiveBacklightRequest, state: AppState = Depends(get_state)
):
    """Arm/disarm Live-view assist lights. Cleared automatically when Live ends."""
    _busy_if_experiment(state)
    mode = await state.hw.set_live_backlight(body.mode)
    return LiveBacklightResponse(mode=mode)


@router.get("/frame.jpg")
async def preview_frame(state: AppState = Depends(get_state)):
    try:
        frame = await state.hw.preview_frame()
    except CameraUnavailableError:
        raise HTTPException(503, "camera not connected")
    return Response(frame, media_type="image/jpeg")


@router.get("/test-photo")
async def test_photo(source: str, zoom: int = 1, state: AppState = Depends(get_state)):
    """Growth config screen: preview a capture lit by IR or a fixed-intensity
    top-down RGBW flash, matching how a night-phase Growth photo would look."""
    if state.runner.status.state in (
        ExperimentState.running,
        ExperimentState.paused,
        ExperimentState.finishing,
    ):
        raise HTTPException(409, "an experiment is running")
    if source not in ("ir", "rgbw"):
        raise HTTPException(400, "source must be 'ir' or 'rgbw'")
    if zoom not in (1, 2):
        raise HTTPException(400, "zoom must be 1 or 2")
    try:
        frame = await state.hw.test_capture(source, zoom)
    except CameraUnavailableError:
        raise HTTPException(503, "camera not connected")
    return Response(frame, media_type="image/jpeg")


@router.post("/test-photo")
async def test_photo_with_settings(settings: CameraSettings, state: AppState = Depends(get_state)):
    """Camera settings screen: one-shot capture with unsaved camera params."""
    if state.runner.status.state in (ExperimentState.running, ExperimentState.paused):
        raise HTTPException(409, "cannot take a test photo while an experiment is running")
    try:
        frame = await state.hw.capture_test_jpeg(settings)
    except CameraUnavailableError:
        raise HTTPException(503, "camera not connected")
    return Response(frame, media_type="image/jpeg")
