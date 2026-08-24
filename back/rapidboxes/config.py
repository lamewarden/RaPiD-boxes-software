"""Application configuration (infrastructure-level, from env / .env).

Distinct from `DeviceSettings` in models.py, which are the user-editable hardware
defaults persisted to a JSON file and exposed over the API.
"""
from __future__ import annotations

import platform
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


def _auto_simulation() -> bool:
    """Default to simulation unless we're on a Linux box with picamera2 available.

    Keeps laptop development safe (no hardware) while letting the Pi run real
    hardware without extra config. Can always be overridden by RAPIDBOXES_SIMULATION.
    """
    if platform.system() != "Linux":
        return True
    try:
        import picamera2  # noqa: F401  (Pi-only, present on device)

        return False
    except Exception:
        return True


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAPIDBOXES_",
        env_file=".env",
        extra="ignore",
    )

    # Hardware mode. True -> fully simulated backends (no Pi required).
    simulation: bool = _auto_simulation()

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8000

    # Where experiment folders and images are written.
    storage_root: Path = Path.home() / "rapidboxes" / "experiments"

    # Persisted, user-editable device settings (camera/leds/ir). Created on first run.
    settings_path: Path = Path.home() / "rapidboxes" / "settings.json"

    # Per-researcher saved camera baseline (Settings -> Camera -> "Save Mine"),
    # keyed by username. Distinct from settings_path: that one is reset to the
    # system default every process start; this one only changes when a user
    # explicitly saves over it. See rapidboxes/camera_defaults.py.
    camera_defaults_path: Path = Path.home() / "rapidboxes" / "camera_user_defaults.json"

    # Built React SPA (dist/spa). When set and present, it is served at "/".
    # In dev we leave this unset and use the Vite dev server + proxy instead.
    spa_dir: Optional[Path] = None

    # Live preview (MJPEG) target frame rate.
    preview_fps: float = 5.0

    # Branch this device tracks for the OTA self-update feature (Settings ->
    # General -> Update button, and the monthly rapidboxes-update.timer).
    # Judgment call: defaults to "main" as the intended long-term stable
    # branch, but whatever branch is actually checked out on a given device
    # may differ (e.g. this repo is developed on "v2" while "main" is the
    # older/stable branch) -- override with RAPIDBOXES_UPDATE_BRANCH so
    # `git fetch origin <branch>` / `git merge --ff-only origin/<branch>`
    # compares against the branch this deployment is meant to track, not
    # blindly against origin/main.
    update_branch: str = "main"

    # Record of applied OTA updates/rollbacks (see rapidboxes/update_history.py),
    # used by Settings -> General -> Version to show "running commit X for Y"
    # and to know what the "Roll back" button targets. Same directory
    # convention as settings_path.
    update_history_path: Path = Path.home() / "rapidboxes" / "update_history.json"

    # Remote CIFS sync (Settings -> General -> Remote Sync). Only the
    # non-secret half lives here -- server, CIFS username, on/off, researcher.
    # The password is session-only and is never written to this (or any) file;
    # see rapidboxes/remote_sync.py.
    remote_sync_path: Path = Path.home() / "rapidboxes" / "remote_sync.json"

    def ensure_dirs(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_config() -> AppConfig:
    cfg = AppConfig()
    cfg.ensure_dirs()
    return cfg
