"""Live camera preview: MJPEG stream + single snapshot."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import Response, StreamingResponse

from .deps import AppState, get_state

log = logging.getLogger("rapidboxes.preview")
router = APIRouter(prefix="/api/preview", tags=["preview"])

_BOUNDARY = "frame"


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


@router.get("")
async def preview_stream(state: AppState = Depends(get_state)):
    return StreamingResponse(
        _mjpeg(state),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
    )


@router.get("/frame.jpg")
async def preview_frame(state: AppState = Depends(get_state)):
    frame = await state.hw.preview_frame()
    return Response(frame, media_type="image/jpeg")
