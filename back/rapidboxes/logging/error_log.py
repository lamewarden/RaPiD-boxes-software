"""Structured error records (errors.jsonl)."""
from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, Optional

from .. import __version__
from .context import base_fields
from .jsonl import exception_fields, utc_now_iso
from .writers import get_errors_writer

log = logging.getLogger("rapidboxes.log")


def _caller_where(skip: int = 2) -> Dict[str, Any]:
    frame = inspect.currentframe()
    for _ in range(skip):
        if frame is None:
            break
        frame = frame.f_back
    if frame is None:
        return {}
    return {
        "module": frame.f_globals.get("__name__", ""),
        "function": frame.f_code.co_name,
        "line": frame.f_lineno,
        "pathname": frame.f_code.co_filename,
    }


def log_error(
    category: str,
    event: str,
    message: str,
    *,
    exc: Optional[BaseException] = None,
    level: str = "ERROR",
    where: Optional[Dict[str, Any]] = None,
    fsync: bool = True,
    **context: Any,
) -> None:
    """Write a structured error entry. Also emits a stdlib log line."""
    writer = get_errors_writer()
    record: Dict[str, Any] = {
        "ts": utc_now_iso(),
        "level": level,
        "category": category,
        "event": event,
        "message": message,
        "where": where or _caller_where(),
        "context": context or None,
        "software_version": __version__,
        **base_fields(),
    }
    exc_fields = exception_fields(exc)
    if exc_fields:
        record["exception"] = exc_fields
    if writer is not None:
        try:
            writer.write(record, fsync=fsync)
        except Exception:
            log.exception("failed to write error log record")
    stdlib_level = getattr(logging, level, logging.ERROR)
    log.log(stdlib_level, "[%s] %s: %s", category, event, message, exc_info=exc)
