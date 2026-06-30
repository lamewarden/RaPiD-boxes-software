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

    async def rebuild_hardware(self, settings: DeviceSettings) -> None:
        """Swap in fresh hardware after a settings change (idle only).

        Must release the old camera/LEDs/IR first: picamera2 raises "Device or
        resource busy" if a second Picamera2() is opened while the first is
        still held. Also configures the new camera immediately, rather than
        leaving it on Picamera2Camera's internal defaults until the next
        experiment start — otherwise a Live preview taken before then would
        silently ignore whatever was just saved (e.g. grayscale/color mode).
        """
        await self.hw.shutdown()
        self.settings = settings
        self.hw = build_hardware(self.config, settings)
        self.runner._hw = self.hw
        await self.hw.configure_camera()


def get_state(request: Request) -> AppState:
    return request.app.state.app
