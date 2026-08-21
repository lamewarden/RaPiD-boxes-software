"""System info for the kiosk header."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import shutil
import socket
import time
from typing import Tuple

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


def _ssh_user() -> str:
    """The account SSH would log into -- same one rapidboxes.service runs as
    (User=@USER@ in deploy/rapidboxes.service), read in-process rather than
    from config since it needs no new setting."""
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return os.environ.get("USER", "")


# /api/system is polled every 5s by the kiosk (see useSystemInfo); SSH
# enabled/disabled essentially never changes at runtime, so cache the
# subprocess result briefly rather than forking `systemctl` on every poll.
_SSH_ENABLED_CACHE_S = 30.0
_ssh_enabled_cache: Tuple[float, bool] = (0.0, False)


def _ssh_enabled() -> bool:
    """Whether openssh-server is actually reachable right now.

    Raspberry Pi OS (Debian-based, Bookworm included) registers the openssh
    server as the systemd unit `ssh.service`, not `sshd.service` -- confirmed
    against Raspberry Pi OS Bookworm docs/forums, which consistently use
    `systemctl enable ssh` / `systemctl status ssh` (unit description "OpenBSD
    Secure Shell server"); `sshd` is not the unit name there even though the
    daemon binary itself is /usr/sbin/sshd. `is-active` (not `is-enabled`) is
    used because what the UI cares about is "can I ssh in right now", not
    just whether it's set to start on boot -- and both are queryable as a
    non-root user. On a dev laptop without systemd (e.g. macOS), this just
    fails closed to False.
    """
    global _ssh_enabled_cache
    checked_at, cached = _ssh_enabled_cache
    now = time.monotonic()
    if now - checked_at < _SSH_ENABLED_CACHE_S:
        return cached

    try:
        result = subprocess.run(
            ["systemctl", "is-active", "ssh"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        enabled = result.stdout.strip() == "active"
    except Exception:
        enabled = False

    _ssh_enabled_cache = (now, enabled)
    return enabled


def _info(state: AppState) -> SystemInfo:
    usage = shutil.disk_usage(state.config.storage_root)
    return SystemInfo(
        hostname=socket.gethostname(),
        ip=_local_ip(),
        version=__version__,
        simulation=state.config.simulation,
        storageRoot=str(state.config.storage_root),
        diskFreeBytes=usage.free,
        diskTotalBytes=usage.total,
        cameraAvailable=state.hw.camera_available,
        sshUser=_ssh_user(),
        sshEnabled=_ssh_enabled(),
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


@router.post("/restart-service")
async def restart_service(state: AppState = Depends(get_state)):
    """Request a full backend restart via systemd.

    The service unit uses Restart=always, so killing this process is enough.
    Delay briefly so the HTTP response can flush, then SIGKILL immediately.

    Do not use SIGTERM here: uvicorn graceful shutdown waits on open kiosk
    WebSockets and MJPEG preview streams, which leaves the UI hung on
    "restarting" for a long time even when Restart=always is set.
    """
    pid = os.getpid()
    loop = asyncio.get_running_loop()

    def _kill() -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    loop.call_later(0.25, _kill)
    return {"status": "restarting"}
