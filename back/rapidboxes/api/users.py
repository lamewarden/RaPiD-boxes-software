"""Known usernames -- top-nav "select user" picker.

No user accounts exist; "known" just means this device has seen the name
before, either as an experiment's owner or as someone who saved personal
settings ("Mine"). Offering a pick-list beats always typing a name from
scratch.

Usernames are matched case-insensitively everywhere (this listing, "Mine"
lookups, and new experiments -- see client/lib/session.ts) -- "Ivan",
"IVAN" and "ivan" are all the same person, one working folder, no separate
"already exists" confirmation needed.
"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends

from .. import user_defaults
from ..models import UserSummary
from ..storage import ExperimentDir
from .deps import AppState, get_state

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=List[UserSummary])
async def list_users(state: AppState = Depends(get_state)) -> List[UserSummary]:
    counts: Dict[str, int] = {}
    for d in state.storage.list_experiments():
        name = (ExperimentDir(d).username() or "").strip().lower()
        if name:
            counts[name] = counts.get(name, 0) + 1
    for key in user_defaults.load_all(state.config.user_defaults_path):
        counts.setdefault(key, 0)
    return [
        UserSummary(username=name, experimentCount=count) for name, count in sorted(counts.items())
    ]
