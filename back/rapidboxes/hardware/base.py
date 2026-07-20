"""Hardware backend interfaces and shared colour helpers.

Three device families, each behind a tiny synchronous interface. Real (Pi) and
simulated implementations both satisfy these; the async HardwareManager wraps them
with a lock + thread executor so the asyncio engine never blocks.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Tuple

from ..models import CameraSettings

RGBW = Tuple[int, int, int, int]
BLACK: RGBW = (0, 0, 0, 0)


def spectra_to_color(spectra: List[str], intensity: int) -> RGBW:
    """Combine selected spectra into one additive RGBW colour at the given intensity.

    intensity is 0-100; each selected channel is driven to that fraction of 255.
    Mirrors the legacy palette (red/green/blue on their channels, white on W).
    """
    v = round(max(0, min(100, intensity)) / 100 * 255)
    return (
        v if "red" in spectra else 0,
        v if "green" in spectra else 0,
        v if "blue" in spectra else 0,
        v if "white" in spectra else 0,
    )


def white(intensity: int) -> RGBW:
    """Top-down white illumination on the W channel."""
    v = round(max(0, min(100, intensity)) / 100 * 255)
    return (0, 0, 0, v)


def zoom_crop_box(width: int, height: int, zoom: float) -> tuple[int, int, int, int]:
    """The (x0, y0, x1, y1) box a `width` x `height` frame is cropped to for
    `zoom`. 1/zoom of each dimension, centered. zoom<=1 is a no-op box (the
    full frame) so callers can skip cropping entirely without a special case."""
    if zoom <= 1.0:
        return (0, 0, width, height)
    cw = max(1, round(width / zoom))
    ch = max(1, round(height / zoom))
    x0 = (width - cw) // 2
    y0 = (height - ch) // 2
    return (x0, y0, x0 + cw, y0 + ch)


class CameraUnavailableError(RuntimeError):
    """Raised when no camera hardware is detected (e.g. ribbon unplugged)."""


class CameraBackend(ABC):
    @abstractmethod
    def configure(self, settings: CameraSettings) -> None: ...

    @abstractmethod
    def capture_file(self, path: str) -> None:
        """Capture a full-resolution still to `path` (blocking)."""

    @abstractmethod
    def capture_jpeg(self, zoom: int = 1) -> bytes:
        """Capture a single preview-resolution JPEG frame (blocking).

        zoom=2 center-crops to half width/height before the preview resize
        (used by the Growth "Test Photo x2" button to check focus/framing).
        """

    @abstractmethod
    def capture_test_jpeg(self, settings: CameraSettings) -> bytes:
        """Reconfigure with `settings` and capture one full-resolution JPEG
        (blocking) — used by the Camera Settings "Test Photo" button to
        preview in-progress, possibly-unsaved settings before committing."""

    @abstractmethod
    def close(self) -> None: ...


class NullCamera(CameraBackend):
    """Stand-in used when no camera is detected at startup.

    Keeps the rest of the app (LEDs, IR, API, UI) usable; anything that
    actually needs a frame gets a clear CameraUnavailableError instead of
    crashing the process.
    """

    def configure(self, settings: CameraSettings) -> None:
        pass

    def capture_file(self, path: str) -> None:
        raise CameraUnavailableError("no camera detected")

    def capture_jpeg(self, zoom: int = 1) -> bytes:
        raise CameraUnavailableError("no camera detected")

    def capture_test_jpeg(self, settings: CameraSettings) -> bytes:
        raise CameraUnavailableError("no camera detected")

    def close(self) -> None:
        pass


class LedBackend(ABC):
    @abstractmethod
    def fill(self, color: RGBW, stride: int = 1) -> None:
        """Set every pixel to `color`; if stride > 1, only every Nth pixel lights
        (the rest are driven off) counting from index 0."""

    @abstractmethod
    def set_segment(self, start: int, end: int, color: RGBW, stride: int = 1) -> None:
        """Set pixels [start, end) to `color` and show. If stride > 1, only every
        Nth pixel within the segment lights (the rest are driven off), counting
        from `start`."""

    @abstractmethod
    def off(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class NullLeds(LedBackend):
    def fill(self, color: RGBW, stride: int = 1) -> None:
        pass

    def set_segment(self, start: int, end: int, color: RGBW, stride: int = 1) -> None:
        pass

    def off(self) -> None:
        pass

    def close(self) -> None:
        pass


class IrBackend(ABC):
    @abstractmethod
    def on(self) -> None: ...

    @abstractmethod
    def off(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class NullIr(IrBackend):
    def on(self) -> None:
        pass

    def off(self) -> None:
        pass

    def close(self) -> None:
        pass
