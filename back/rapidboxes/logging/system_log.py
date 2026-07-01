"""Operational system events (system.jsonl)."""
from __future__ import annotations

import logging
from typing import Any, Dict

from .. import __version__
from .context import base_fields
from .jsonl import utc_now_iso
from .writers import get_system_writer

log = logging.getLogger("rapidboxes.system")


def log_system(event: str, message: str, **context: Any) -> None:
    writer = get_system_writer()
    record: Dict[str, Any] = {
        "ts": utc_now_iso(),
        "event": event,
        "message": message,
        "software_version": __version__,
        **base_fields(),
    }
    if context:
        record["context"] = context
    if writer is not None:
        try:
            writer.write(record)
        except Exception:
            log.exception("failed to write system log record")
    log.info("%s: %s", event, message)
