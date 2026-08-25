"""Synology DSM sharing-link configuration (Settings -> General -> Sharing
Links). Same shape as api/remote_sync.py -- a separate router so the
password stays well away from DeviceSettings, accepted only on PUT, never
returned by any route here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..models import DsmSharingStatus, DsmSharingUpdate, validate_dsm_host, validate_dsm_share_root
from .deps import AppState, get_state

router = APIRouter(prefix="/api/settings/dsm-sharing", tags=["dsm-sharing"])


class CheckConnectionResult(BaseModel):
    ok: bool
    message: str
    status: DsmSharingStatus


@router.get("", response_model=DsmSharingStatus)
async def get_dsm_sharing(state: AppState = Depends(get_state)):
    return state.dsm_sharing.status()


@router.put("", response_model=DsmSharingStatus)
async def put_dsm_sharing(update: DsmSharingUpdate, state: AppState = Depends(get_state)):
    dsm = state.dsm_sharing
    settings = dsm.settings

    if update.host is not None:
        if update.host.strip():
            try:
                settings.host = validate_dsm_host(update.host)
            except ValueError as e:
                raise HTTPException(400, str(e))
        else:
            settings.host = ""
    if update.port is not None:
        if not (1 <= update.port <= 65535):
            raise HTTPException(400, "port must be between 1 and 65535")
        settings.port = update.port
    if update.username is not None:
        settings.username = update.username.strip()
    if update.shareRoot is not None:
        if update.shareRoot.strip():
            try:
                settings.shareRoot = validate_dsm_share_root(update.shareRoot)
            except ValueError as e:
                raise HTTPException(400, str(e))
        else:
            settings.shareRoot = ""
    if update.password is not None:
        dsm.set_password(update.password)

    if update.enabled is not None:
        if update.enabled:
            if not settings.host or not settings.username or not dsm.password_set:
                raise HTTPException(400, "a host, username and password are required to switch this on")
            if not settings.shareRoot:
                raise HTTPException(400, "a DSM share root is required to switch this on")
            settings.enabled = True
        else:
            settings.enabled = False
            dsm.clear_password()

    dsm.persist()
    return dsm.status()


@router.post("/check", response_model=CheckConnectionResult)
async def check_connection(state: AppState = Depends(get_state)):
    dsm = state.dsm_sharing
    if not dsm.settings.host or not dsm.settings.username or not dsm.password_set:
        raise HTTPException(400, "a host, username and password are required")
    ok, message = await dsm.check_connection()
    return CheckConnectionResult(ok=ok, message=message, status=dsm.status())
