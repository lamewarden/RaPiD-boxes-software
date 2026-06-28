"""Real RGBW NeoPixel backend via SPI (Adafruit), Pi 4 and Pi 5 compatible.

The legacy embedded `rpi_ws281x` PWM/DMA driver (GPIO18) does NOT work on Pi 5
(RP1). Driving WS281x/SK6812 over SPI (data on GPIO10 / SPI0 MOSI) works on both
Pi 4 and Pi 5, so this is the portable path. RGBW is selected via pixel_order.
"""
from __future__ import annotations

import logging

from ..models import LedSettings
from .base import RGBW, BLACK, LedBackend

log = logging.getLogger("rapidboxes.leds")


class NeoPixelSpiLeds(LedBackend):
    def __init__(self, settings: LedSettings):
        import board  # Pi-only (Blinka)
        import neopixel_spi as neopixel  # adafruit-circuitpython-neopixel-spi

        self._settings = settings
        order = getattr(neopixel, settings.pixelOrder, neopixel.GRBW)
        spi = board.SPI()
        self._np = neopixel.NeoPixel_SPI(
            spi,
            settings.pixelCount,
            pixel_order=order,
            auto_write=False,
            frequency=settings.spiHz,
        )
        self.off()

    def fill(self, color: RGBW) -> None:
        self._np.fill(color)
        self._np.show()

    def set_segment(self, start: int, end: int, color: RGBW) -> None:
        start = max(0, start)
        end = min(self._settings.pixelCount, end)
        for i in range(start, end):
            self._np[i] = color
        self._np.show()

    def off(self) -> None:
        self._np.fill(BLACK)
        self._np.show()

    def close(self) -> None:
        try:
            self.off()
        except Exception:  # pragma: no cover
            log.exception("led close failed")
