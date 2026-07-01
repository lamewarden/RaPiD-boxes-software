"""Crash-safe JSON Lines writer with size-based rotation."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonlWriter:
    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ):
        self._path = path
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def write(self, record: Dict[str, Any], *, fsync: bool = False) -> None:
        line = json.dumps(record, default=str, ensure_ascii=False) + "\n"
        data = line.encode("utf-8")
        with self._lock:
            self._rotate_if_needed(len(data))
            with self._path.open("ab") as f:
                f.write(data)
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())

    def _rotate_if_needed(self, incoming: int) -> None:
        if not self._path.exists():
            return
        if self._path.stat().st_size + incoming <= self._max_bytes:
            return
        for i in range(self._backup_count - 1, 0, -1):
            src = self._path.with_suffix(self._path.suffix + f".{i}")
            dst = self._path.with_suffix(self._path.suffix + f".{i + 1}")
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
        first = self._path.with_suffix(self._path.suffix + ".1")
        if first.exists():
            first.unlink()
        self._path.rename(first)


def exception_fields(exc: Optional[BaseException]) -> Optional[Dict[str, Any]]:
    if exc is None:
        return None
    import traceback

    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }
