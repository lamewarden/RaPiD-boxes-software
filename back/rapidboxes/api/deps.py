"""Shared application state and FastAPI dependencies."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from ..config import AppConfig
from ..engine.runner import ExperimentRunner
from ..hardware.manager import HardwareManager, build_hardware
from ..models import DeviceSettings
from ..storage import Storage


@dataclass
class AppState:
    config: AppConfig
    settings: DeviceSettings
    storage: Storage
    hw: HardwareManager
    runner: ExperimentRunner

    def rebuild_hardware(self, settings: DeviceSettings) -> None:
        """Swap in fresh hardware after a settings change (idle only)."""
        self.settings = settings
        self.hw = build_hardware(self.config, settings)
        self.runner._hw = self.hw


def get_state(request: Request) -> AppState:
    return request.app.state.app
