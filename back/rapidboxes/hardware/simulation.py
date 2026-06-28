"""Simulated hardware backends so the full stack runs on a laptop (no Pi).

The camera synthesizes labelled JPEG frames; LEDs and IR just record state and log.
"""
from __future__ import annotations

import io
import logging
import math
import time
from datetime import datetime

from PIL import Image, ImageDraw

from ..models import CameraSettings
from .base import RGBW, BLACK, CameraBackend, IrBackend, LedBackend

log = logging.getLogger("rapidboxes.sim")


class SimCamera(CameraBackend):
    """Generates deterministic-ish images annotated with the current state."""

    def __init__(self, illumination_probe=None):
        self._settings = CameraSettings()
        self._frame = 0
        # callable returning a short string describing current light state, for overlays
        self._probe = illumination_probe or (lambda: "")

    def configure(self, settings: CameraSettings) -> None:
        self._settings = settings

    def _render(self, width: int, height: int) -> Image.Image:
        s = self._settings
        bg = 20 if s.grayscale else 30
        img = Image.new("RGB", (width, height), (bg, bg, bg + (0 if s.grayscale else 10)))
        d = ImageDraw.Draw(img)
        # A fake "seedling" that drifts a little over time so timelapses look alive.
        cx = width // 2 + int(math.sin(self._frame / 6.0) * width * 0.05)
        base_y = int(height * 0.85)
        top_y = int(height * 0.3 + math.cos(self._frame / 9.0) * height * 0.04)
        stem = (90, 140, 70) if not s.grayscale else (150, 150, 150)
        d.line([(width // 2, base_y), (cx, top_y)], fill=stem, width=max(3, width // 120))
        d.ellipse(
            [cx - width // 40, top_y - width // 40, cx + width // 40, top_y + width // 40],
            fill=stem,
        )
        # Overlay metadata.
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "RaPiD-box SIMULATION",
            f"frame {self._frame}  {ts}",
            f"{width}x{height} exp={s.exposureMicroseconds}us iso={s.iso}",
            f"light: {self._probe()}",
        ]
        d.multiline_text((10, 10), "\n".join(lines), fill=(0, 255, 0))
        return img

    def capture_file(self, path: str) -> None:
        time.sleep(0.05)  # pretend a capture takes a moment
        img = self._render(self._settings.width, self._settings.height)
        img.save(path, "JPEG", quality=self._settings.jpegQuality)
        self._frame += 1
        log.info("sim capture -> %s", path)

    def capture_jpeg(self) -> bytes:
        img = self._render(640, 360)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        return buf.getvalue()

    def close(self) -> None:
        log.info("sim camera closed")


class SimLeds(LedBackend):
    def __init__(self, pixel_count: int = 70):
        self.pixels = [BLACK] * pixel_count

    def fill(self, color: RGBW) -> None:
        self.pixels = [color] * len(self.pixels)
        log.info("sim leds fill %s", color)

    def set_segment(self, start: int, end: int, color: RGBW) -> None:
        start = max(0, start)
        end = min(len(self.pixels), end)
        for i in range(start, end):
            self.pixels[i] = color
        log.info("sim leds [%d:%d] = %s", start, end, color)

    def off(self) -> None:
        self.pixels = [BLACK] * len(self.pixels)
        log.info("sim leds off")

    def close(self) -> None:
        self.off()


class SimIr(IrBackend):
    def __init__(self):
        self.state = False

    def on(self) -> None:
        self.state = True
        log.info("sim IR on")

    def off(self) -> None:
        self.state = False
        log.info("sim IR off")

    def close(self) -> None:
        self.off()
