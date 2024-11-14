import RPi.GPIO as GPIO
import time
import picamera
from PIL import Image, ImageEnhance
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
import imageio
import sys
import signal
import shutil



LED_COUNT = 70  # Number of LED pixels.
LED_PIN = 18  # GPIO pin connected to the pixels (real nuber is 12) (must support PWM!).
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






def colorWipe(strip, palette, wait_ms=50, strip_length=[0, 64], step=3):
    """Updated color Wipe.
    Wipe color across display a pixel at a time"""
    for i in range(strip_length[0],strip_length[1], step):   # range of illuminated LEDs is defined
        strip.setPixelColor(i, palette)
        strip.show()
        time.sleep(wait_ms / 1000.0)

def ir_image(image_name, shutter_speed = 6000000, framerate = 0.1, fr=8, iso=300, sleep_time = 8):
# Activate GPIO26 and set it high  # Let the camera warm up
# Initialize GPIO settings
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(26, GPIO.OUT)
    GPIO.output(26, GPIO.HIGH)
    # colorWipe(strip, Color(0, green, 0, 0), 0)
    with picamera.PiCamera() as camera:
        camera.color_effects = (128, 128)  # b/w mode
        camera.resolution = (3280, 2464)
        camera.framerate = framerate
        camera.shutter_speed = shutter_speed  # exposure length, can be ajusted (max 6000000 - 6 sec)
        camera.exposure_mode = 'off'  # turning off of autoexposure
        camera.iso = iso
                    # Give the camera a good long time to measure AWB
                    # (you may wish to use fixed AWB instead)
        camera.awb_mode = 'off'
        camera.awb_gains = (fr, fr)
        time.sleep(sleep_time)

        # Capture the photo
        # image_name = f"photo_fr_{fr}_fps_{framerate}_shutter_{shutter_speed}_iso{iso}.jpg"
        camera.capture(image_name)
    GPIO.output(26, GPIO.LOW)
    GPIO.cleanup()

# Deactivate GPIO26


stacked_image = None
for i in range(20):
    image_name=str(i)+"frame.jpg"
    ir_image(image_name=image_name)
    # cutting redundant parts
    image = Image.open(image_name)
    image_np = np.array(image)
    cut_image = image_np[500:-200,500:-500]

    if stacked_image is None:
        stacked_image = np.zeros_like(cut_image, dtype=np.float32)

    stacked_image += cut_image
    print(f"stacked image {i}")
    # Calculate the average
averaged_image = stacked_image / 8

# Clip values to valid range (0-255) and convert back to uint8
averaged_image = np.clip(averaged_image, 0, 255).astype(np.uint8)
# Save the enhanced image using PIL
enhanced_image = Image.fromarray(averaged_image)
enhanced_image.save("./img_stack/enhanced_image.jpg")



print(f"Photo saved as {image_name}")

