"""HardwareManager: the single, serialized owner of all devices.

The asyncio engine talks only to this. Every device call is run in a thread
executor under one lock, so blocking camera/LED operations never stall the event
loop and can never race. shutdown() guarantees lights-off + camera-release.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Literal, Optional

from ..config import AppConfig
from ..models import (
    CameraSettings,
    DeviceSettings,
    IrSettings,
    LedSettings,
    PhotoIlluminationSource,
    exposure_for_source,
)
from .base import (
    CameraBackend,
    CameraUnavailableError,
    HardwareTimeoutError,
    IrBackend,
    LedBackend,
    NullCamera,
    NullIr,
    NullLeds,
    RGBW,
    spectra_to_color,
    white,
)

log = logging.getLogger("rapidboxes.hw")

# Fixed dim fill for Live "White backlight" (R,G,B,W). All channels = 10.
LIVE_WHITE_BACKLIGHT: RGBW = (10, 10, 10, 10)
# Live assist is RGBW-only; IR focusing stays on Settings → test photo.
LiveBacklightMode = Literal["off", "white"]

# LED/IR calls are GPIO/SPI and should always return in well under a second;
# 5s is a generous margin before we treat one as stuck.
DEFAULT_TIMEOUT = 5.0
# Camera calls can legitimately take a while: up to a 10s IR exposure plus a
# 2s settle plus readout/encode/save overhead. 20s covers the slowest real
# capture with room to spare.
CAMERA_TIMEOUT = 20.0


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
        # Set once a hardware call times out -- see _run's own comment for
        # why. Once True, every hardware call fails fast instead of risking
        # a genuine concurrent call into the same device; recovering requires
        # a service restart. See DEBUG_HANDOUT.md #1.9.
        self._wedged = False
        self.light_desc = "off"
        self.camera_available = camera_available
        self._live_backlight: Optional[Literal["white"]] = None
        # Live uses short RGBW exposure; invalidated when still/test config is applied.
        self._preview_camera_ready = False

        # Let the simulated camera annotate frames with the current light state.
        from .simulation import SimCamera

        if isinstance(camera, SimCamera):
            camera._probe = lambda: self.light_desc

    async def _run(self, fn, *args, timeout: float = DEFAULT_TIMEOUT):
        # A wedged manager fails fast, before even trying the lock -- see
        # the timeout branch below for why this exists.
        if self._wedged:
            raise HardwareTimeoutError(
                "a previous hardware call timed out and this device is presumed "
                "wedged -- restart the service"
            )
        loop = asyncio.get_event_loop()
        async with self._lock:
            try:
                return await asyncio.wait_for(loop.run_in_executor(None, fn, *args), timeout=timeout)
            except asyncio.TimeoutError:
                name = getattr(fn, "__qualname__", repr(fn))
                # run_in_executor's underlying thread cannot be cancelled --
                # giving up on awaiting it does not stop it, so it may well
                # still be inside the blocking driver call indefinitely.
                # `async with` is about to release the lock regardless; if we
                # let the next caller acquire it and submit a NEW call here,
                # that call runs genuinely concurrently with the stuck one --
                # exactly the race this class's own docstring promises can
                # never happen ("Every device call is run in a thread
                # executor under one lock ... can never race"). Wedging the
                # whole manager, not just retrying, is what actually keeps
                # that promise once a timeout has already shown the hardware
                # is misbehaving. See DEBUG_HANDOUT.md #1.9.
                self._wedged = True
                log.error(
                    "hardware call %s timed out after %.0fs; treating this device as "
                    "wedged -- every further hardware call will fail fast until the "
                    "service restarts",
                    name,
                    timeout,
                )
                raise HardwareTimeoutError(f"{name} timed out after {timeout:.0f}s") from None

    def _live_preview_settings(self) -> CameraSettings:
        """Still framing/focus from device settings, but always RGBW-speed exposure.

        Live must stay snappy even when photoIlluminationSource is IR (multi-second
        stills). IR exposure is only used for Settings test photos / experiments.
        """
        cam = self._settings.camera
        exp = exposure_for_source("rgbw", cam.exposureMicroseconds)
        if exp == cam.exposureMicroseconds:
            return cam
        return cam.model_copy(update={"exposureMicroseconds": exp})

    async def _ensure_live_preview_camera(self) -> None:
        if not self.camera_available or self._preview_camera_ready:
            return
        await self._run(self._camera.configure, self._live_preview_settings(), timeout=CAMERA_TIMEOUT)
        self._preview_camera_ready = True

    def restore_experiment_settings(
        self, camera: CameraSettings, photo_illumination_source: PhotoIlluminationSource
    ) -> None:
        """Override the camera + illumination-source half of the live session
        settings with what one specific experiment was actually using.

        The session's camera settings reset to the system default on every
        process start (see settings_store.load_device_settings_for_new_session)
        -- deliberately, for a fresh start, but wrong for engine.runner.recover()
        resuming an already-running experiment: without this, the very next
        configure_camera() would silently swap a resumed run's exposure/zoom/
        color mode to whatever the fresh session defaults to. LEDs/IR pin
        wiring is deliberately left alone: that's a device property, not a
        per-experiment choice, and restoring an old experiment's copy of it
        would revert a legitimate rewiring made since.

        Caller must still await configure_camera() to apply this to the
        physical camera.
        """
        self._settings = DeviceSettings(
            camera=camera,
            leds=self._settings.leds,
            ir=self._settings.ir,
            photoIlluminationSource=photo_illumination_source,
        )

    # --- lifecycle -------------------------------------------------------
    async def configure_camera(self) -> None:
        if not self.camera_available:
            return
        await self._run(self._camera.configure, self._settings.camera, timeout=CAMERA_TIMEOUT)
        self._preview_camera_ready = False

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
        await self._run(self._camera.capture_file, path, timeout=CAMERA_TIMEOUT)

    async def preview_frame(self, zoom: int = 1) -> bytes:
        await self._ensure_live_preview_camera()
        return await self._run(self._camera.capture_jpeg, zoom, timeout=CAMERA_TIMEOUT)

    async def capture_test_jpeg(self, settings: CameraSettings) -> bytes:
        """Camera Settings test photo: same illumination a real dark/baseline/night
        capture would use — the persisted photoIlluminationSource setting, not the
        camera's colour mode. Camera settings themselves may be unsaved edits."""
        from ..models import PHOTO_FLASH_INTENSITY

        self._preview_camera_ready = False
        if self.photo_illumination_source == "ir":
            await self.ir_on()
            try:
                return await self._run(self._camera.capture_test_jpeg, settings, timeout=CAMERA_TIMEOUT)
            finally:
                await self.ir_off()
        await self.top_white(PHOTO_FLASH_INTENSITY)
        try:
            return await self._run(self._camera.capture_test_jpeg, settings, timeout=CAMERA_TIMEOUT)
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
            camera = await self._run(Picamera2Camera, timeout=CAMERA_TIMEOUT)
        except (CameraUnavailableError, HardwareTimeoutError):
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
    @property
    def _stride(self) -> int:
        return self._settings.leds.stride

    async def top_white(self, intensity: int) -> None:
        seg = self._settings.leds.topSegment
        await self._run(self._leds.set_segment, seg[0], seg[1], white(intensity), self._stride)
        self.light_desc = f"white@{intensity}%"

    async def top(self, spectra: List[str], intensity: int) -> None:
        seg = self._settings.leds.topSegment
        color = spectra_to_color(spectra, intensity)
        await self._run(self._leds.set_segment, seg[0], seg[1], color, self._stride)
        self.light_desc = f"{'+'.join(spectra)}@{intensity}% (top)"

    async def lateral(self, spectra: List[str], intensity: int) -> None:
        seg = self._settings.leds.lateralSegment
        color = spectra_to_color(spectra, intensity)
        await self._run(self._leds.set_segment, seg[0], seg[1], color, self._stride)
        self.light_desc = f"{'+'.join(spectra)}@{intensity}%"

    async def leds_off(self) -> None:
        await self._run(self._leds.off)
        self.light_desc = "off"

    def _leds_and_ir_off(self) -> None:
        """Both device calls in one function, so `all_off()` submits them as
        a single `_run()`/lock acquisition -- see its own docstring."""
        self._leds.off()
        self._ir.off()

    async def all_off(self) -> None:
        """Turns LEDs and IR off as one atomic hardware call.

        Previously two separate `_run()` calls (via leds_off() then
        ir_off()), each independently acquiring and releasing `self._lock` --
        so another coroutine's call could interleave between them, and "all
        off" was never actually a state a caller could rely on having
        reached, despite `_capture` using it as step 1 of its imaging
        sequence on that assumption. See DEBUG_HANDOUT.md #1.10.
        """
        await self._run(self._leds_and_ir_off)
        self.light_desc = "off"
        self._live_backlight = None

    # --- settings snapshots (for status/history) --------------------------
    @property
    def settings(self) -> DeviceSettings:
        """The live settings actually driving the hardware right now -- not
        necessarily identical to what was passed at construction, since
        restore_experiment_settings() can override the camera/source half
        afterwards (see engine.runner.recover()). main.py reads this after
        recover() to keep AppState.settings (GET /api/settings, the Camera
        Settings UI) in step with the hardware instead of showing whatever
        was true before that override."""
        return self._settings

    @property
    def photo_illumination_source(self) -> str:
        return self._settings.photoIlluminationSource

    @property
    def led_settings(self) -> LedSettings:
        return self._settings.leds

    @property
    def ir_settings(self) -> IrSettings:
        return self._settings.ir

    async def set_live_backlight(self, mode: LiveBacklightMode) -> LiveBacklightMode:
        """Live-view assist light: dim RGBW fill, or off. No IR on Live."""
        if mode == "off":
            await self.clear_live_backlight()
            return "off"
        await self.ir_off()
        await self._run(self._leds.fill, LIVE_WHITE_BACKLIGHT, self._stride)
        self._live_backlight = "white"
        self.light_desc = "live-white"
        return "white"

    async def clear_live_backlight(self) -> None:
        """Turn off Live assist lights if any were armed; no-op otherwise."""
        if self._live_backlight is None:
            return
        await self.all_off()


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
        try:
            leds = NeoPixelSpiLeds(settings.leds)
        except Exception as exc:
            log.warning("visible LEDs unavailable; continuing without them: %s", exc)
            leds = NullLeds()
        try:
            ir = GpioIr(settings.ir.pins)
        except Exception as exc:
            log.warning("IR outputs unavailable; continuing without them: %s", exc)
            ir = NullIr()
    return HardwareManager(camera, leds, ir, settings, camera_available=camera_available)
