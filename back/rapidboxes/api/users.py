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

from dataclasses import dataclass
from typing import Dict, List

from fastapi import APIRouter, Depends

from .. import user_defaults
from ..models import UserSummary
from ..storage import ExperimentDir
from .deps import AppState, get_state

router = APIRouter(prefix="/api/users", tags=["users"])


@dataclass
class _Tally:
    count: int = 0
    bytes_used: int = 0


@router.get("", response_model=List[UserSummary])
async def list_users(state: AppState = Depends(get_state)) -> List[UserSummary]:
    tallies: Dict[str, _Tally] = {}
    for d in state.storage.list_experiments():
        exp = ExperimentDir(d)
        name = (exp.username() or "").strip().lower()
        if not name:
            continue
        tally = tallies.setdefault(name, _Tally())
        tally.count += 1
        tally.bytes_used += exp.size_bytes()
    for key in user_defaults.load_all(state.config.user_defaults_path):
        tallies.setdefault(key, _Tally())
    return [
        UserSummary(username=name, experimentCount=t.count, bytesUsed=t.bytes_used)
        for name, t in sorted(tallies.items())
    ]
