"""QA assistant chat endpoint.

Runs against a remote API (e-INFRA CZ's gateway), not a local model, so
there's no RAM/CPU contention with a live run to guard against -- chat is
reachable whether an experiment is running or not. (An earlier local-model
version gated this to idle-only for that reason; see git history / the
migration commit for why that no longer applies.)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..assistant.service import AssistantUnavailable
from ..models import AssistantChatRequest, AssistantChatResponse, SavedExperimentConfig
from .deps import AppState, get_state

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.post("/chat", response_model=AssistantChatResponse)
async def chat(body: AssistantChatRequest, state: AppState = Depends(get_state)):
    try:
        return await state.assistant.chat(body.message, body.history, body.username)
    except AssistantUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


class PendingLaunchResponse(BaseModel):
    config: Optional[SavedExperimentConfig] = None


@router.get("/pending-launch", response_model=PendingLaunchResponse)
async def get_pending_launch(
    username: str = Query(min_length=1, max_length=40),
    protocol: Optional[str] = Query(
        None,
        description=(
            "Only consume a staged config matching this protocol -- the Tropism and Growth setup "
            "screens both poll this on mount, and without this filter whichever loads first would "
            "silently discard a config meant for the other."
        ),
    ),
    state: AppState = Depends(get_state),
):
    """One-shot: a config staged by Telegram's /launch wizard once someone
    confirms it (see AssistantService.stage_pending_launch) -- consumed the
    moment the matching setup screen reads it, so a second call for the
    same username returns null, not the same config again."""
    return PendingLaunchResponse(config=state.assistant.take_pending_launch(username, protocol))
