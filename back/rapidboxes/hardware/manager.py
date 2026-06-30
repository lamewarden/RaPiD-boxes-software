"""HardwareManager: the single, serialized owner of all devices.

The asyncio engine talks only to this. Every device call is run in a thread
executor under one lock, so blocking camera/LED operations never stall the event
loop and can never race. shutdown() guarantees lights-off + camera-release.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List

from ..config import AppConfig
from ..models import DeviceSettings
from .base import (
    CameraBackend,
    CameraUnavailableError,
    IrBackend,
    LedBackend,
    NullCamera,
    spectra_to_color,
    white,
)

log = logging.getLogger("rapidboxes.hw")


class HardwareManager:
    def __init__(
        self,
        camera: CameraBackend,
        leds: LedBackend,
        ir: IrBackend,
        settings: DeviceSettings,
        camera_available: bool = True,
    ):
        self._camera = camera
        self._leds = leds
        self._ir = ir
        self._settings = settings
        self._lock = asyncio.Lock()
        self.light_desc = "off"
        self.camera_available = camera_available

        # Let the simulated camera annotate frames with the current light state.
        from .simulation import SimCamera

        if isinstance(camera, SimCamera):
            camera._probe = lambda: self.light_desc

    async def _run(self, fn, *args):
        loop = asyncio.get_event_loop()
        async with self._lock:
            return await loop.run_in_executor(None, fn, *args)

    # --- lifecycle -------------------------------------------------------
    async def configure_camera(self) -> None:
        if not self.camera_available:
            return
        await self._run(self._camera.configure, self._settings.camera)

    async def shutdown(self) -> None:
        """Best-effort: turn everything off, then release devices. Never raises."""
        try:
            await self.all_off()
        except Exception:
            log.exception("all_off during shutdown failed")
        for closer in (self._leds.close, self._ir.close, self._camera.close):
            try:
                await self._run(closer)
            except Exception:
                log.exception("device close failed")

    # --- camera ----------------------------------------------------------
    async def capture(self, path: str) -> None:
        await self._run(self._camera.capture_file, path)

    async def preview_frame(self, zoom: int = 1) -> bytes:
        return await self._run(self._camera.capture_jpeg, zoom)

    async def test_capture(self, source: str, zoom: int = 1) -> bytes:
        """One-off preview capture lit the same way a Growth night photo would be."""
        from ..models import GROWTH_PHOTO_FLASH_INTENSITY

        if source == "ir":
            await self.ir_on()
            try:
                return await self.preview_frame(zoom)
            finally:
                await self.ir_off()
        else:  # rgbw
            await self.top_white(GROWTH_PHOTO_FLASH_INTENSITY)
            try:
                return await self.preview_frame(zoom)
            finally:
                await self.all_off()

    async def recheck_camera(self) -> bool:
        """Try to pick up a camera plugged in after startup.

        picamera2/libcamera enumerate the CSI sensor once and don't notice a
        hot-plugged camera on their own, so a fresh Picamera2() probe is the
        only way to find out a camera showed up without restarting the
        service. No-op (returns True) if one is already available.
        """
        if self.camera_available:
            return True
        from .camera import Picamera2Camera

        try:
            camera = await self._run(Picamera2Camera)
        except CameraUnavailableError:
            return False
        self._camera = camera
        self.camera_available = True
        await self.configure_camera()
        return True

    # --- IR --------------------------------------------------------------
    async def ir_on(self) -> None:
        await self._run(self._ir.on)
        self.light_desc = "IR"

    async def ir_off(self) -> None:
        await self._run(self._ir.off)
        if self.light_desc == "IR":
            self.light_desc = "off"

    # --- visible LEDs ----------------------------------------------------
    async def top_white(self, intensity: int) -> None:
        seg = self._settings.leds.topSegment
        await self._run(self._leds.set_segment, seg[0], seg[1], white(intensity))
        self.light_desc = f"white@{intensity}%"

    async def top(self, spectra: List[str], intensity: int) -> None:
        seg = self._settings.leds.topSegment
        color = spectra_to_color(spectra, intensity)
        await self._run(self._leds.set_segment, seg[0], seg[1], color)
        self.light_desc = f"{'+'.join(spectra)}@{intensity}% (top)"

    async def lateral(self, spectra: List[str], intensity: int) -> None:
        seg = self._settings.leds.lateralSegment
        color = spectra_to_color(spectra, intensity)
        await self._run(self._leds.set_segment, seg[0], seg[1], color)
        self.light_desc = f"{'+'.join(spectra)}@{intensity}%"

    async def leds_off(self) -> None:
        await self._run(self._leds.off)
        self.light_desc = "off"

    async def all_off(self) -> None:
        await self.leds_off()
        await self.ir_off()


def build_hardware(config: AppConfig, settings: DeviceSettings) -> HardwareManager:
    """Construct the manager with real (Pi) or simulated backends."""
    camera_available = True
    if config.simulation:
        from .simulation import SimCamera, SimIr, SimLeds

        log.info("hardware: SIMULATION mode")
        camera: CameraBackend = SimCamera()
        leds: LedBackend = SimLeds(settings.leds.pixelCount)
        ir: IrBackend = SimIr()
    else:
        from .camera import Picamera2Camera
        from .ir import GpioIr
        from .leds import NeoPixelSpiLeds

        log.info("hardware: REAL device mode")
        try:
            camera = Picamera2Camera()
        except CameraUnavailableError:
            log.warning("no camera detected; continuing without it (capture disabled)")
            camera = NullCamera()
            camera_available = False
        leds = NeoPixelSpiLeds(settings.leds)
        ir = GpioIr(settings.ir.pins)
    return HardwareManager(camera, leds, ir, settings, camera_available=camera_available)
