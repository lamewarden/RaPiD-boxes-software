"""Experiment step journal (experiment.jsonl)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .. import __version__
from .context import base_fields, next_step_index
from .jsonl import utc_now_iso
from .writers import get_journal_writer

log = logging.getLogger("rapidboxes.journal")

VALID_STATUSES = frozenset({"started", "done", "stopped", "crashed", "skipped"})


def journal_step(
    step_id: str,
    status: str,
    *,
    detail: Optional[str] = None,
    error: Optional[str] = None,
    fsync: bool = False,
    **extra: Any,
) -> None:
    """Record one experiment step transition in the journal."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid journal status {status!r}; allowed: {sorted(VALID_STATUSES)}")

    writer = get_journal_writer()
    record: Dict[str, Any] = {
        "ts": utc_now_iso(),
        "step_id": step_id,
        "step_index": next_step_index(),
        "status": status,
        "software_version": __version__,
        **base_fields(),
    }
    if detail:
        record["detail"] = detail
    if error:
        record["error"] = error
    if extra:
        record["extra"] = extra

    if writer is not None:
        try:
            writer.write(record, fsync=fsync or status in ("crashed", "stopped"))
        except Exception:
            log.exception("failed to write experiment journal record")

    log.info("journal %s %s%s", step_id, status, f" ({error})" if error else "")
