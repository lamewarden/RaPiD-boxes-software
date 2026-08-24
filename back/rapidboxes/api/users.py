"""Known researcher usernames -- top-nav "select user" picker.

No user accounts exist; "known" just means this device has seen the name
before, either as an experiment's owner or as someone who saved personal
settings ("Mine"). Offering a pick-list beats always typing a name from
scratch, and keeps a researcher's various experiments under one consistent
spelling instead of "Ivan"/"ivan"/"IVAN" fragmenting their history.
"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends

from .. import user_defaults
from ..storage import ExperimentDir
from .deps import AppState, get_state

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=List[str])
async def list_users(state: AppState = Depends(get_state)) -> List[str]:
    # Prefer the casing a researcher actually typed (from their most recent
    # experiment) over user_defaults.json's lowercased keys, which only exist
    # to make "Mine" lookups case-insensitive and never preserve casing.
    by_key: Dict[str, str] = {}
    for d in state.storage.list_experiments():
        name = (ExperimentDir(d).username() or "").strip()
        if name:
            by_key.setdefault(name.lower(), name)
    for key in user_defaults.load_all(state.config.user_defaults_path):
        by_key.setdefault(key, key)
    return sorted(by_key.values(), key=str.lower)
