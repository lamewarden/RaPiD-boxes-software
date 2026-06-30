"""System info for the kiosk header."""
from __future__ import annotations

import shutil
import socket

from fastapi import APIRouter, Depends

from .. import __version__
from ..models import SystemInfo
from .deps import AppState, get_state

router = APIRouter(prefix="/api/system", tags=["system"])


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _info(state: AppState) -> SystemInfo:
    usage = shutil.disk_usage(state.config.storage_root)
    return SystemInfo(
        hostname=socket.gethostname(),
        ip=_local_ip(),
        version=__version__,
        simulation=state.config.simulation,
        diskFreeBytes=usage.free,
        diskTotalBytes=usage.total,
        cameraAvailable=state.hw.camera_available,
    )


@router.get("", response_model=SystemInfo)
async def system(state: AppState = Depends(get_state)):
    return _info(state)


@router.post("/recheck-camera", response_model=SystemInfo)
async def recheck_camera(state: AppState = Depends(get_state)):
    """Re-probe for a camera plugged in after the backend started."""
    await state.hw.recheck_camera()
    return _info(state)
