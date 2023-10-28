#!/bin/python3

import tkinter as tk
from tkinter import ttk
import tkinter.font as font
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
from fish import *
import imageio
import sys
import signal

# setting working directory
os.chdir('/home/pi/Camera/RaPiD-boxes-software/GUI')

# LED strip configuration:
LED_COUNT = 9  # Number of LED pixels.
# LED_PIN = 18  # NEAR RGB
LED_PIN = 21    # FAR RGB 
LED_FREQ_HZ = 800000  # LED signal frequency in hertz (usually 800khz)
LED_DMA = 10  # DMA channel to use for generating signal (try 10)
LED_BRIGHTNESS = 255  # Set to 0 for darkest and 255 for brightest
LED_INVERT = False  # True to invert the signal (when using NPN transistor level shift)
LED_CHANNEL = 0
LED_STRIP = ws.SK6812W_STRIP

# Create NeoPixel object with appropriate configuration.
strip = Adafruit_NeoPixel(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL,
                          LED_STRIP)
# Intialize the library (must be called once before other functions).
strip.begin()
# strip_near.begin()

# IR LEDs configuration
# GPIO.setwarnings(False)
# GPIO.setmode(GPIO.BCM)
# GPIO.setup(26, GPIO.OUT)  # inderpendent IR board # 37
# GPIO.setup(23, GPIO.OUT)  # second IR board # 16



def streaming(strip, timer=20000):
    colorWipe(strip, Color(0, 0, 0, 0), 0)
    colorWipe(strip, Color(0, 0, 0, 0), strip_length=[0, 9], step=2)
    # subprocess.call("raspistill -t {}".format(timer), shell=True)
    # camera = picamera.PiCamera()
    # camera.resolution = '1280 x 720'
    # camera.start_preview()
    time.sleep(timer)
    # camera.stop_preview()
    colorWipe(strip, Color(0, 0, 0, 0), 0)




def colorWipe(strip, palette=Color(0, 0, 0, 0), wait_ms=50, strip_length=[0, 64], step=1):
    """Updated color Wipe.
    Wipe color across display a pixel at a time"""
    for i in range(strip_length[0],strip_length[1], step):   # range of illuminated LEDs is defined
        strip.setPixelColor(i, palette)
        strip.show()
        time.sleep(wait_ms / 1000.0)


streaming(strip)