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
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
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
    return Response(
        frame,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.post("/test-photo")
async def test_photo_with_settings(settings: CameraSettings, state: AppState = Depends(get_state)):
    """Camera settings screen: one-shot capture with unsaved camera params.

    Illumination follows the persisted photoIlluminationSource setting (IR vs
    RGBW top flash, at the configured LED stride) — the same illumination a
    real dark/baseline/night capture would use. Camera settings may be unsaved
    edits from the editor; illumination is not editable from this screen.
    """
    if state.runner.status.state in (ExperimentState.running, ExperimentState.paused):
        raise HTTPException(409, "cannot take a test photo while an experiment is running")
    try:
        frame = await state.hw.capture_test_jpeg(settings)
    except CameraUnavailableError:
        raise HTTPException(503, "camera not connected")
    return Response(frame, media_type="image/jpeg")

