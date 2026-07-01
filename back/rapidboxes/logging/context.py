"""Correlation context carried on every log record (run, experiment, phase)."""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any, Dict, Optional

_run_id: ContextVar[str] = ContextVar("run_id", default="")
_experiment_id: ContextVar[Optional[str]] = ContextVar("experiment_id", default=None)
_protocol: ContextVar[Optional[str]] = ContextVar("protocol", default=None)
_phase: ContextVar[Optional[str]] = ContextVar("phase", default=None)
_step_index: ContextVar[int] = ContextVar("step_index", default=0)


def new_run_id() -> str:
    return str(uuid.uuid4())


def set_run_id(run_id: str) -> None:
    _run_id.set(run_id)


def get_run_id() -> str:
    return _run_id.get()


def set_experiment_context(
    experiment_id: Optional[str],
    *,
    protocol: Optional[str] = None,
    phase: Optional[str] = None,
) -> None:
    _experiment_id.set(experiment_id)
    if protocol is not None:
        _protocol.set(protocol)
    if phase is not None:
        _phase.set(phase)


def clear_experiment_context() -> None:
    _experiment_id.set(None)
    _protocol.set(None)
    _phase.set(None)


def set_phase(phase: Optional[str]) -> None:
    _phase.set(phase)


def next_step_index() -> int:
    idx = _step_index.get() + 1
    _step_index.set(idx)
    return idx


def reset_step_index() -> None:
    _step_index.set(0)


def base_fields() -> Dict[str, Any]:
    out: Dict[str, Any] = {"run_id": get_run_id()}
    exp = _experiment_id.get()
    if exp:
        out["experiment_id"] = exp
    proto = _protocol.get()
    if proto:
        out["protocol"] = proto
    phase = _phase.get()
    if phase:
        out["phase"] = phase
    return out
