"""Load/save the user-editable DeviceSettings as JSON (atomic write)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .models import CameraSettings, DeviceSettings

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


def load_device_settings_for_new_session(path: Path) -> DeviceSettings:
    """Load settings for a fresh process start, with camera reset to defaults.

    Camera tweaks made from the Camera Settings menu are session-scoped: the
    defaults are the "standard" settings, and any in-session changes must not
    carry over to the next run. LEDs/IR (wiring, not user-tuned per-session)
    are kept as persisted.
    """
    settings = load_device_settings(path)
    # Rebuild rather than model_copy: model_copy skips validation, and the
    # DeviceSettings validator is what re-pairs the fresh camera defaults with
    # the persisted illumination source (an IR run must not come back at flash
    # exposure and capture nothing but black frames).
    settings = DeviceSettings(
        camera=CameraSettings(),
        leds=settings.leds,
        ir=settings.ir,
        photoIlluminationSource=settings.photoIlluminationSource,
    )
    save_device_settings(path, settings)
    return settings
