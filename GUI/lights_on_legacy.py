import tkinter as tk
from tkinter import ttk
import socket
import picamera
import time
import datetime
import RPi.GPIO as GPIO
import os
import timeit
from fractions import Fraction
from rpi_ws281x import *
import numpy as np
import subprocess
from neopixel import *

# LED strip configuration:
# Create NeoPixel object with appropriate configuration.
strip = Adafruit_NeoPixel(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL,
                          LED_STRIP)
# Intialize the library (must be called once before other functions).
strip.begin()

def colorWipe(strip, palette, wait_ms=50):
    """Wipe color across display a pixel at a time."""
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, palette)
        strip.show()
        time.sleep(wait_ms / 1000.0)

colorWipe(strip, Color(255, 255, 0, 0), 0) 
time.sleep(5)