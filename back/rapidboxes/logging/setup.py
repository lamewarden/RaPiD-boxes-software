"""Process-wide logging setup: JSONL sinks + stdlib bridge."""
from __future__ import annotations

import logging
import sys
from typing import Optional

from ..config import AppConfig
from .context import base_fields, new_run_id, set_run_id
from .error_log import log_error
from .jsonl import utc_now_iso
from .system_log import log_system
from .writers import configure_writers, get_errors_writer

_initialized = False


class _JsonlErrorHandler(logging.Handler):
    """Mirror ERROR+ stdlib records into errors.jsonl with location metadata."""

    def emit(self, record: logging.LogRecord) -> None:
        writer = get_errors_writer()
        if writer is None:
            return
        exc_info = record.exc_info
        exc: Optional[BaseException] = None
        if exc_info and exc_info[1] is not None:
            exc = exc_info[1]
        fields = base_fields()
        fields.update(
            {
                "ts": utc_now_iso(),
                "level": record.levelname,
                "category": record.name,
                "event": record.funcName or "log",
                "message": record.getMessage(),
                "where": {
                    "module": record.module,
                    "function": record.funcName,
                    "line": record.lineno,
                    "pathname": record.pathname,
                },
            }
        )
        from .. import __version__

        fields["software_version"] = __version__
        if exc is not None:
            from .jsonl import exception_fields

            fields["exception"] = exception_fields(exc)
        try:
            writer.write(fields, fsync=record.levelno >= logging.ERROR)
        except Exception:
            pass


def _excepthook(exc_type, exc, tb) -> None:
    if _initialized:
        log_error(
            "process",
            "uncaught_exception",
            str(exc),
            exc=exc,
            where={"module": getattr(exc_type, "__module__", ""), "function": "<module>"},
        )
    sys.__excepthook__(exc_type, exc, tb)


def init_logging(config: AppConfig) -> str:
    """Configure JSONL writers and stdlib logging. Returns the new run_id."""
    global _initialized

    configure_writers(config.log_root)

    run_id = new_run_id()
    set_run_id(run_id)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(stream)
    root.addHandler(_JsonlErrorHandler())

    sys.excepthook = _excepthook
    _initialized = True

    log_system(
        "app.startup",
        "logging initialized",
        log_root=str(config.log_root),
        simulation=config.simulation,
        storage_root=str(config.storage_root),
    )
    return run_id


def shutdown_logging() -> None:
    global _initialized
    if _initialized:
        log_system("app.shutdown", "logging shutdown")
    _initialized = False
