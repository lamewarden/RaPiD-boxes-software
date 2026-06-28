"""Real camera backend using picamera2 (Raspberry Pi OS Bookworm, Pi 4/5).

Replaces the legacy `picamera` library (unsupported on Pi 5 / Bookworm). Heavy
imports live inside __init__ so this module stays importable off-device.
"""
from __future__ import annotations

import io
import logging
import time

from ..models import CameraSettings
from .base import CameraBackend

log = logging.getLogger("rapidboxes.camera")


class Picamera2Camera(CameraBackend):
    def __init__(self):
        from picamera2 import Picamera2  # Pi-only

        self._Picamera2 = Picamera2
        self._cam = Picamera2()
        self._settings = CameraSettings()
        self._configured = False

    def _controls(self) -> dict:
        s = self._settings
        # ISO ~= AnalogueGain * 100 on Pi cameras.
        ctrls = {
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": int(s.exposureMicroseconds),
            "AnalogueGain": max(1.0, s.iso / 100.0),
            "ColourGains": (s.awbRedGain, s.awbBlueGain),
        }
        if s.grayscale:
            ctrls["Saturation"] = 0.0
        return ctrls

    def configure(self, settings: CameraSettings) -> None:
        s = settings
        self._settings = s
        still = self._cam.create_still_configuration(
            main={"size": (s.width, s.height)},
            controls=self._controls(),
        )
        if self._configured:
            self._cam.stop()
        self._cam.configure(still)
        self._cam.start()
        self._cam.set_controls(self._controls())
        self._configured = True
        log.info("camera configured %dx%d", s.width, s.height)

    def _ensure(self) -> None:
        if not self._configured:
            self.configure(self._settings)

    def capture_file(self, path: str) -> None:
        self._ensure()
        # Let manual exposure/AWB settle before the still.
        time.sleep(max(0.0, self._settings.settleSeconds))
        self._cam.capture_file(path)
        log.info("captured %s", path)

    def capture_jpeg(self) -> bytes:
        self._ensure()
        from PIL import Image  # available on-device too

        arr = self._cam.capture_array("main")
        img = Image.fromarray(arr).convert("RGB")
        img.thumbnail((640, 360))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        return buf.getvalue()

    def close(self) -> None:
        try:
            self._cam.stop()
            self._cam.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            log.exception("camera close failed")
