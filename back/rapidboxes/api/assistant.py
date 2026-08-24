"""Local QA assistant chat endpoint.

Only reachable while idle: an active experiment locks out chat the same way
Settings does (409), so the model never competes with a live run for this
Pi's RAM/CPU. The moment an experiment starts, api/experiments.py cuts any
in-flight chat short -- see AssistantService.interrupt_and_archive().
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..models import AssistantChatRequest, AssistantChatResponse
from .deps import AppState, get_state

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

# Same set api/experiments.py's free_space() treats as "there's an active run".
ACTIVE_STATES = ("running", "paused", "finishing")


@router.post("/chat", response_model=AssistantChatResponse)
async def chat(body: AssistantChatRequest, state: AppState = Depends(get_state)):
    if state.runner.status.state in ACTIVE_STATES:
        raise HTTPException(409, "assistant chat is unavailable while an experiment is running")
    return await state.assistant.chat(body.message, body.history, body.username)
