"""Load/save the user-editable DeviceSettings as JSON (atomic write)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .models import DeviceSettings

log = logging.getLogger("rapidboxes.settings")


def load_device_settings(path: Path) -> DeviceSettings:
    if path.exists():
        try:
            return DeviceSettings.model_validate_json(path.read_text())
        except Exception:
            log.exception("invalid settings file %s; using defaults", path)
    return DeviceSettings()


def save_device_settings(path: Path, settings: DeviceSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(settings.model_dump_json(indent=2))
    os.replace(tmp, path)
