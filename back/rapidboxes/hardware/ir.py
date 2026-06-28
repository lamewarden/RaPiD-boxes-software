"""Real IR-LED backend via gpiozero (lgpio backend on Bookworm, Pi 4/5).

Replaces the legacy RPi.GPIO usage (unsupported on Pi 5). The two IR boards are
on BCM pins (legacy 26 & 23); only powered during a capture to avoid overheating.
"""
from __future__ import annotations

import logging
from typing import List

from .base import IrBackend

log = logging.getLogger("rapidboxes.ir")


class GpioIr(IrBackend):
    def __init__(self, pins: List[int]):
        from gpiozero import LED  # Pi-only; uses lgpio pin factory on Bookworm

        self._leds = [LED(p) for p in pins]

    def on(self) -> None:
        for led in self._leds:
            led.on()

    def off(self) -> None:
        for led in self._leds:
            led.off()

    def close(self) -> None:
        try:
            self.off()
            for led in self._leds:
                led.close()
        except Exception:  # pragma: no cover
            log.exception("ir close failed")
