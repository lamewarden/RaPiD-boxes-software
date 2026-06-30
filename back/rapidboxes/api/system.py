"""System info for the kiosk header."""
from __future__ import annotations

import os
import signal
import subprocess
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


@router.post("/close-kiosk")
async def close_kiosk(state: AppState = Depends(get_state)):
    """Best-effort close of Chromium launched in kiosk app mode.

    The backend keeps running (systemd service). This endpoint only targets the
    local browser process so the "Close" button can behave like an app exit.
    """
    url = "http://localhost:%d" % state.config.port
    patterns = [
        "chromium.*--app=%s" % url,
        "chromium-browser.*--app=%s" % url,
    ]

    pids = set()
    for pattern in patterns:
        try:
            out = subprocess.check_output(["pgrep", "-f", pattern], text=True)
        except subprocess.CalledProcessError:
            continue
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pid = int(line)
            except ValueError:
                continue
            if pid != os.getpid():
                pids.add(pid)

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    return {"status": "closing", "kioskPids": sorted(pids)}
