"""Per-researcher saved camera baseline ("Save Mine") -- Settings -> Camera.

Distinct from:
  - CameraSettings' own field defaults, the fixed system default that nothing
    in the app can overwrite;
  - the active session's camera settings (settings_store.py), which are
    always reset to the system default at every process start.

This one is keyed by username, persists across restarts, and only changes
when a user explicitly saves over it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

from .models import CameraSettings


def _key(username: str) -> str:
    return username.strip().lower()


def load_all(path: Path) -> Dict[str, CameraSettings]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        return {k: CameraSettings.model_validate(v) for k, v in raw.items()}
    except Exception:
        return {}


def load_for(path: Path, username: str) -> Optional[CameraSettings]:
    return load_all(path).get(_key(username))


def save_for(path: Path, username: str, camera: CameraSettings) -> CameraSettings:
    all_defaults = load_all(path)
    all_defaults[_key(username)] = camera
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({k: v.model_dump(mode="json") for k, v in all_defaults.items()}, indent=2)
    )
    os.replace(tmp, path)
    return camera
