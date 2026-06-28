"""Device settings (camera / LEDs / IR)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..models import DeviceSettings, ExperimentState
from ..settings_store import save_device_settings
from .deps import AppState, get_state

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=DeviceSettings)
async def get_settings(state: AppState = Depends(get_state)):
    return state.settings


@router.put("", response_model=DeviceSettings)
async def put_settings(settings: DeviceSettings, state: AppState = Depends(get_state)):
    if state.runner.status.state in (ExperimentState.running, ExperimentState.paused):
        raise HTTPException(409, "cannot change settings while an experiment is running")
    save_device_settings(state.config.settings_path, settings)
    state.rebuild_hardware(settings)
    return state.settings
