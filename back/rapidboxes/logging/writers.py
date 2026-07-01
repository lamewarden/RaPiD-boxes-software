"""Shared JSONL writer instances (set once at startup)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .jsonl import JsonlWriter

_log_root: Optional[Path] = None
_errors: Optional[JsonlWriter] = None
_journal: Optional[JsonlWriter] = None
_system: Optional[JsonlWriter] = None


def configure_writers(log_root: Path) -> None:
    global _log_root, _errors, _journal, _system
    _log_root = log_root
    log_root.mkdir(parents=True, exist_ok=True)
    _errors = JsonlWriter(log_root / "errors.jsonl")
    _journal = JsonlWriter(log_root / "experiment.jsonl")
    _system = JsonlWriter(log_root / "system.jsonl")


def get_log_root() -> Optional[Path]:
    return _log_root


def get_errors_writer() -> Optional[JsonlWriter]:
    return _errors


def get_journal_writer() -> Optional[JsonlWriter]:
    return _journal


def get_system_writer() -> Optional[JsonlWriter]:
    return _system
