"""Real camera backend using picamera2 (Raspberry Pi OS Bookworm, Pi 4/5).

Replaces the legacy `picamera` library (unsupported on Pi 5 / Bookworm). Heavy
imports live inside __init__ so this module stays importable off-device.
"""
from __future__ import annotations

import io
import logging
import time

from ..models import CameraSettings
from .base import CameraBackend, CameraUnavailableError

log = logging.getLogger("rapidboxes.camera")


class Picamera2Camera(CameraBackend):
    def __init__(self):
        from libcamera import controls as libcamera_controls
        from picamera2 import Picamera2  # Pi-only

        self._libcamera_controls = libcamera_controls
        self._Picamera2 = Picamera2
        try:
            self._cam = Picamera2()
        except IndexError:
            # picamera2 raises this when no sensor is detected on the CSI port
            # (ribbon unplugged, camera not seated, or camera_auto_detect not
            # yet applied since the last config.txt change).
            raise CameraUnavailableError("no camera detected on CSI port") from None
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
            "Saturation": 0.0 if s.grayscale else 1.0,
        }
        if s.autofocusEnabled:
            ctrls["AfMode"] = self._libcamera_controls.AfModeEnum.Continuous
        else:
            ctrls["AfMode"] = self._libcamera_controls.AfModeEnum.Manual
            ctrls["LensPosition"] = float(s.focusDistance)
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
        log.info(
            "camera configured %dx%d (%s)",
            s.width,
            s.height,
            "autofocus" if s.autofocusEnabled else f"manual focus {s.focusDistance:.1f}",
        )

    def _ensure(self) -> None:
        if not self._configured:
            self.configure(self._settings)

    def capture_file(self, path: str) -> None:
        self._ensure()
        # Let manual exposure/AWB settle before the still.
        time.sleep(max(0.0, self._settings.settleSeconds))
        self._cam.capture_file(path)
        log.info("captured %s", path)

    def capture_jpeg(self, zoom: int = 1) -> bytes:
        self._ensure()
        from PIL import Image  # available on-device too

        arr = self._cam.capture_array("main")
        if zoom > 1:
            h, w = arr.shape[:2]
            cw, ch = w // zoom, h // zoom
            x0, y0 = (w - cw) // 2, (h - ch) // 2
            arr = arr[y0 : y0 + ch, x0 : x0 + cw]
        img = Image.fromarray(arr).convert("RGB")
        img.thumbnail((640, 360))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        return buf.getvalue()

    def capture_test_jpeg(self, settings: CameraSettings) -> bytes:
        from PIL import Image  # available on-device too

        self.configure(settings)
        time.sleep(max(0.0, settings.settleSeconds))
        arr = self._cam.capture_array("main")
        img = Image.fromarray(arr).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=settings.jpegQuality)
        return buf.getvalue()

    def close(self) -> None:
        try:
            self._cam.stop()
            self._cam.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            log.exception("camera close failed")
