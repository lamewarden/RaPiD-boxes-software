"""Persist the OTA update history as JSON (atomic write).

Same convention as settings_store.py: a small JSON file under the user's
`~/rapidboxes/` directory, read/modified/written as a whole rather than a
database. This one holds a short list rather than a single object, since the
UI needs to know both the current entry (what commit, applied when, by what
trigger) and the previous one (what the "Roll back" button targets).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List

from .models import UpdateHistoryEntry

log = logging.getLogger("rapidboxes.update_history")

# A rolling "recent versions" log, not a full audit trail -- cap growth so it
# never becomes a maintenance concern on a box that's been auto-updating
# monthly for years.
MAX_ENTRIES = 50


def load_history(path: Path) -> List[UpdateHistoryEntry]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
        return [UpdateHistoryEntry.model_validate(item) for item in raw]
    except Exception:
        log.exception("invalid update history file %s; treating as empty", path)
        return []


def save_history(path: Path, entries: List[UpdateHistoryEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = json.dumps([e.model_dump(mode="json") for e in entries[-MAX_ENTRIES:]], indent=2)
    tmp.write_text(payload)
    os.replace(tmp, path)


def append_entry(path: Path, entry: UpdateHistoryEntry) -> UpdateHistoryEntry:
    entries = load_history(path)
    entries.append(entry)
    save_history(path, entries)
    return entry
