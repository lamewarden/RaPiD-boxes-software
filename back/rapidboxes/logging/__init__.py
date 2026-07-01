"""Structured logging for RaPiD-boxes: errors, experiment journal, system events.

All records are written as JSON Lines under the configured log root. Call
``init_logging`` once at process startup (see ``main.py`` lifespan).
"""
from __future__ import annotations

from .context import clear_experiment_context, reset_step_index, set_experiment_context, set_phase
from .error_log import log_error
from .experiment_journal import journal_step
from .setup import init_logging, shutdown_logging
from .system_log import log_system

__all__ = [
    "init_logging",
    "shutdown_logging",
    "log_error",
    "journal_step",
    "log_system",
    "set_experiment_context",
    "clear_experiment_context",
    "reset_step_index",
    "set_phase",
]
