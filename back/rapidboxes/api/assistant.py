"""QA assistant chat endpoint.

Runs against a remote API (e-INFRA CZ's gateway), not a local model, so
there's no RAM/CPU contention with a live run to guard against -- chat is
reachable whether an experiment is running or not. (An earlier local-model
version gated this to idle-only for that reason; see git history / the
migration commit for why that no longer applies.)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..assistant.service import AssistantUnavailable
from ..models import AssistantChatRequest, AssistantChatResponse
from .deps import AppState, get_state

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.post("/chat", response_model=AssistantChatResponse)
async def chat(body: AssistantChatRequest, state: AppState = Depends(get_state)):
    try:
        return await state.assistant.chat(body.message, body.history, body.username)
    except AssistantUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
