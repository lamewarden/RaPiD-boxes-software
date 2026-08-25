"""Telegram issue-alert linking -- Settings -> General -> Telegram Alerts.

See rapidboxes/telegram_link.py for the linking flow itself; this router is
just the thin HTTP surface over it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models import TelegramLinkCodeResponse, TelegramStatusResponse
from .deps import AppState, get_state

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.get("/status", response_model=TelegramStatusResponse)
async def status(
    username: str = Query(min_length=1, max_length=40), state: AppState = Depends(get_state)
):
    return TelegramStatusResponse(
        configured=state.telegram.configured,
        linked=state.telegram.is_linked(username),
        botUsername=state.telegram.bot_username,
    )


@router.post("/link-code", response_model=TelegramLinkCodeResponse)
async def link_code(
    username: str = Query(min_length=1, max_length=40), state: AppState = Depends(get_state)
):
    if not state.telegram.configured:
        raise HTTPException(409, "Telegram alerts are not set up on this device yet")
    code = state.telegram.request_link_code(username)
    return TelegramLinkCodeResponse(code=code, botUsername=state.telegram.bot_username or "")
