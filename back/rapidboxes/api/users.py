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

from typing import List

from fastapi import APIRouter, Depends

from ..models import UserSummary
from ..storage import tally_by_user
from .deps import AppState, get_state

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=List[UserSummary])
async def list_users(state: AppState = Depends(get_state)) -> List[UserSummary]:
    tallies = tally_by_user(state.storage, state.config.user_defaults_path)
    return [
        UserSummary(username=name, experimentCount=t.count, bytesUsed=t.bytes_used)
        for name, t in sorted(tallies.items())
    ]
