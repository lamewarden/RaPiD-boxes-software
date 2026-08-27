"""Device settings (camera / LEDs / IR)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import user_defaults
from ..models import DeviceSettings, UserDefaultsUpdate, is_experiment_active
from ..settings_store import save_device_settings
from .deps import AppState, get_state

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=DeviceSettings)
async def get_settings(state: AppState = Depends(get_state)):
    return state.settings


@router.put("", response_model=DeviceSettings)
async def put_settings(settings: DeviceSettings, state: AppState = Depends(get_state)):
    if is_experiment_active(state.runner.status.state):
        raise HTTPException(409, "cannot change settings while an experiment is running")
    save_device_settings(state.config.settings_path, settings)
    await state.rebuild_hardware(settings)
    return state.settings


@router.get("/mine", response_model=Optional[DeviceSettings])
async def get_my_defaults(
    username: str = Query(min_length=1, max_length=40), state: AppState = Depends(get_state)
):
    return user_defaults.load_for(state.config.user_defaults_path, username)


@router.put("/mine", response_model=DeviceSettings)
async def put_my_defaults(body: UserDefaultsUpdate, state: AppState = Depends(get_state)):
    return user_defaults.save_for(state.config.user_defaults_path, body.username, body.settings)
