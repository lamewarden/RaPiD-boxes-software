"""Grabs a real screenshot of whatever's currently on the kiosk's own
touchscreen -- for remote troubleshooting over Telegram ("what does the
screen actually show right now"), distinct from AssistantService.
capture_snapshot, which captures from the *camera*, not the display.

The kiosk runs Chromium in kiosk mode under labwc (a wlroots Wayland
compositor) -- confirmed live on the real device, not assumed: `grim` is
the standard wlroots screenshot tool and is already installed. The
rapidboxes.service unit already runs as the same user as the interactive
kiosk session (`rp`), so no privilege escalation is needed -- it just needs
the same XDG_RUNTIME_DIR/WAYLAND_DISPLAY environment a systemd *system*
service doesn't inherit from that user's own login session, which is
supplied explicitly here rather than assumed present.

Never invents anything: this is a real, unmodified grab of the actual
compositor output, not a mock or a placeholder image.
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("rapidboxes.kiosk_screenshot")

# labwc's own default output socket name; this device only ever runs one
# compositor session, so there's nothing to discover dynamically.
_WAYLAND_DISPLAY = "wayland-0"
_TIMEOUT_S = 10.0


class KioskScreenshotUnavailable(Exception):
    """grim isn't installed, or there's no reachable Wayland session (e.g.
    a dev laptop, or the kiosk display isn't up) -- never a crash, always a
    plain reason to relay back to whoever asked."""


async def capture_kiosk_screenshot() -> bytes:
    """Returns a real PNG of the current kiosk screen. Raises
    KioskScreenshotUnavailable with a human-readable reason on any failure
    -- missing binary, no compositor socket, or a hung/timed-out call."""
    runtime_dir = f"/run/user/{os.getuid()}"
    socket_path = os.path.join(runtime_dir, _WAYLAND_DISPLAY)
    if not os.path.exists(socket_path):
        raise KioskScreenshotUnavailable(
            "no kiosk display session found (this isn't the real device, or the kiosk isn't running)"
        )

    env = {**os.environ, "XDG_RUNTIME_DIR": runtime_dir, "WAYLAND_DISPLAY": _WAYLAND_DISPLAY}
    try:
        proc = await asyncio.create_subprocess_exec(
            "grim",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        raise KioskScreenshotUnavailable("the screenshot tool (grim) isn't installed on this device") from None

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise KioskScreenshotUnavailable("timed out capturing the screen") from None

    if proc.returncode != 0 or not stdout:
        reason = stderr.decode("utf-8", errors="replace").strip() or f"exit code {proc.returncode}"
        log.warning("grim failed: %s", reason)
        raise KioskScreenshotUnavailable(f"could not capture the screen: {reason}")
    return stdout
