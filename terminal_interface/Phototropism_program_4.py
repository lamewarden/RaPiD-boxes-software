__author__ = 'Lamewarden'

import sys  # library for arguments
import picamera
import time
import datetime
import RPi.GPIO as GPIO
import os
import timeit
from fractions import Fraction
from distutils.dir_util import copy_tree
import subprocess
from neopixel import *

# arguments order scriptname.py a x y z name
# a - 1 as yes and 0 as no, argument which turns off apical hook opening tracing (making pictures of it)
# x - how many hours before Blue light on (hours)
# y - period between pictures (min) (universal throughout experiment)
# z - for how long we want blue LEDs on
# name - as data naming failed - we will ask user to name resulting folder
# user - lets sort content by users

apical_decision = int(sys.argv[1])  # a argument which turns off apical hook opening tracing (making pictures of it)
total_hours = float(sys.argv[2])  # x how many hours before Blue light on (hours)
period_min = float(sys.argv[3])  # y (universal)that is period between pictures (min)
total_hours_blue = float(sys.argv[4])  # z for how long we want blue LEDs on (hours)
folder_name = str(sys.argv[5])  # name of the resulting folder
user_name = str(sys.argv[6])
color = str(sys.argv[7])  # color of the color strip
light_intensity = int(sys.argv[8])  # intensity of the light

total_minutes = total_hours * 60  # necessary for counting number of pictures		initial value is 60
period_sec = period_min * 60  # (universal) sleep counts time in secs
# camera = PiCamera()
pic_num = int(total_minutes // period_min)  # takes only int part of quotient

# blue part
total_minutes_blue = total_hours_blue * 60  # initial value is 60
pic_num_blue = int(total_minutes_blue // period_min)

# Color and intensity definition:
palette = {"red": (light_intensity, 0, 0, 0), "green": (0, light_intensity, 0, 0), "blue": (0, 0, light_intensity, 0),
           "white": (0, 0, 0, light_intensity), "mixwhite": (light_intensity, light_intensity, light_intensity, 0),
           "brutalwhite": (light_intensity, light_intensity, light_intensity, light_intensity)}
for a in palette:
    if color == a:
        r, g, b, w = palette[a]

# LED strip configuration:
LED_COUNT = 35  # Number of LED pixels.
LED_PIN = 18  # GPIO pin connected to the pixels (real nuber is 12) (must support PWM!).
LED_FREQ_HZ = 800000  # LED signal frequency in hertz (usually 800khz)
LED_DMA = 10  # DMA channel to use for generating signal (try 10)
LED_BRIGHTNESS = 255  # Set to 0 for darkest and 255 for brightest
LED_INVERT = False  # True to invert the signal (when using NPN transistor level shift)
LED_CHANNEL = 0
LED_STRIP = ws.SK6812W_STRIP


# Define functions which animate LEDs in various ways.
def colorWipe(strip, color, wait_ms=50):
    """Wipe color across display a pixel at a time."""
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, color)
        strip.show()
        time.sleep(wait_ms / 1000.0)


# Create NeoPixel object with appropriate configuration.
strip = Adafruit_NeoPixel(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL, LED_STRIP)
# Intialize the library (must be called once before other functions).
strip.begin()

# IR LEDs configuration
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)
GPIO.setup(37, GPIO.OUT)  # inderpendent IR
GPIO.setup(16, GPIO.OUT)  # second IR

# We should move to the mounted directory
# folder where it mounted
# os.chdir("/mnt/Shared/Pictures/Raps_pi/PI4")
os.chdir("/home/pi/camera/Experiments")

if os.path.isdir("{}".format(user_name)):
    os.chdir("{}".format(user_name))
else:
    os.mkdir("{}".format(user_name))
    os.chdir("{}".format(user_name))

# that will release us from constant deleting of already existing folders
if os.path.isdir("{}".format(folder_name)):
    print("Folder with such name already exists")
    time.sleep(5)
else:
    os.mkdir("{}".format(folder_name))
    os.chdir("{}".format(folder_name))
    # if int(total_hours) != 0:
    # 	os.mkdir("Darkness")
    # 	os.chdir("Darkness")

# initial titled photo
colorWipe(strip, Color(r, g, b, w), 0)  # White wipe

with picamera.PiCamera() as camera:
    camera.resolution = (3280, 2464)
    camera.framerate = 0.2
    camera.shutter_speed = 700000
    camera.exposure_mode = 'off'
    camera.iso = 200
    time.sleep(5)
    camera.capture('initial_pic.jpg')
# Switching off LED strip
colorWipe(strip, Color(0, 0, 0, 0), 0)

# stupid counter might be useful in the future
# with open('stupid_counter.txt', 'w') as file:
#     file.write('Begin\n')
for i in range(pic_num):
    start_time = timeit.default_timer()  # when making of picture starts
    if int(apical_decision) == 1:  # by this, we disarm the whole cycle
        GPIO.output(16, GPIO.HIGH)
        GPIO.output(37, GPIO.HIGH)
        with picamera.PiCamera() as camera:
            camera.color_effects = (128, 128)  # b/w mode
            camera.resolution = (3280, 2464)
            camera.framerate = 0.2
            camera.shutter_speed = 400000  # exposure length, can be ajusted (max 6000000 - 6 sec)
            camera.exposure_mode = 'off'  # turning off of autoexposure
            camera.iso = 100
            # Give the camera a good long time to measure AWB
            # (you may wish to use fixed AWB instead)
            camera.awb_mode = 'off'
            camera.awb_gains = (Fraction(2), Fraction(1))
            camera.capture("./{}_dark.jpg".format(i))
        GPIO.output(16, GPIO.LOW)
        GPIO.output(37, GPIO.LOW)
    # Making image of current state, while dark stage is ongoing
    else:
        # Image of current plant look
        GPIO.output(16, GPIO.HIGH)
        GPIO.output(37, GPIO.HIGH)
        with picamera.PiCamera() as camera:
            camera.color_effects = (128, 128)  # b/w mode
            camera.resolution = (3280, 2464)
            camera.framerate = 0.2
            camera.shutter_speed = 400000  # exposure length, can be ajusted (max 6000000 - 6 sec)
            camera.exposure_mode = 'off'  # turning off of autoexposure
            camera.iso = 100
            # Give the camera a good long time to measure AWB
            # (you may wish to use fixed AWB instead)
            camera.awb_mode = 'off'
            camera.awb_gains = (Fraction(2), Fraction(1))
            camera.capture("./current_look_step{}(dark_stage).jpg".format(i))
        GPIO.output(16, GPIO.LOW)
        GPIO.output(37, GPIO.LOW)
        # current photo should be only one
        exists = os.path.isfile("./current_look_step{}(dark_stage).jpg".format(i-1))
        if exists:
            os.remove("./current_look_step{}(dark_stage).jpg".format(i-1))
        # with open('stupid_counter.txt', 'a') as file:
        #     file.write('\n Dark cycle {number}. Goes normally so far. System time {time}'.format(number=i,
        #                                                                                           time=datetime.datetime.now()))
        # adjustment of total time(cause it tends to run forward for ~45 sec per cycle
    elapsed = timeit.default_timer() - start_time
    time.sleep(float(period_sec) - elapsed)

GPIO.cleanup()

### Bending part


GPIO.setmode(GPIO.BOARD)
GPIO.setup(16, GPIO.OUT)  # with blue
# GPIO.setup(18, GPIO.OUT)  # blue LEDs
GPIO.setup(37, GPIO.OUT)  # inderpendent IR

# that will release us from constant deleting of already existing folders
if (total_hours_blue) != 0:
    # if (total_hours) != 0:
    # 	os.chdir("../")
    # os.mkdir("Unilateral_Blue")
    # os.chdir("Unilateral_Blue")
    for i in range(pic_num_blue):
        start_time = timeit.default_timer()  # when making of picture starts
        colorWipe(strip, Color(0, 0, 0, 0), 0)  # light
        GPIO.output(16, GPIO.HIGH)
        GPIO.output(37, GPIO.HIGH)
        with picamera.PiCamera() as camera:
            camera.color_effects = (128, 128)  # b/w mode
            camera.resolution = (3280, 2464)
            camera.framerate = 0.2
            camera.shutter_speed = 400000  # exposure length, can be ajusted (max 6000000 - 6 sec)
            camera.exposure_mode = 'off'  # turning off of autoexposure
            camera.iso = 100
            # Give the camera a good long time to measure AWB
            # (you may wish to use fixed AWB instead)
            camera.awb_mode = 'off'
            camera.awb_gains = (Fraction(2), Fraction(1))

            camera.capture("./{}_{}_irradiated.jpg".format(i, color))
        colorWipe(strip, Color(r, g, b, w), 0)
        GPIO.output(16, GPIO.LOW)
        GPIO.output(37, GPIO.LOW)
        # adjustment of total time(cause it tends to run forward for ~45 sec per cycle
        elapsed = timeit.default_timer() - start_time
        time.sleep(float(period_sec) - elapsed)

    GPIO.cleanup()
    colorWipe(strip, Color(0, 0, 0, 0), 0)

# Current working dirrectory for current experiment
# os.chdir("../")
current_workdir = os.getcwd()
# Now let's mount disc through bash and copy all we need
subprocess.call("sudo mount -t cifs //ds.asuch.cas.cz/ueb/lhr  /mnt/Shared -o user=LHR,pass=nMajF8 &> /dev/null",
                shell=True)  # here we adress to bash and mount network drive
# now we have to move to the mounted dir to make it available
os.chdir("/mnt/Shared/Pictures/Raps_pi/PI4")
# We check if server folder contains folder with our's username
if os.path.isdir("/mnt/Shared/Pictures/Raps_pi/PI4/{username}".format(username=user_name)):
    subprocess.call("sudo cp -r {cwdir} /mnt/Shared/Pictures/Raps_pi/PI4/{username}".format(username=user_name,
                                                                                            cwdir=current_workdir),
                    shell=True)
    # os.chdir("/mnt/Shared/Pictures/Raps_pi/PI4/{username}".format(username = user_name))
else:
    subprocess.call("sudo mkdir /mnt/Shared/Pictures/Raps_pi/PI4/{username}".format(username=user_name), shell=True)
    subprocess.call("sudo cp -r {cwdir} /mnt/Shared/Pictures/Raps_pi/PI4/{username}".format(username=user_name,
                                                                                            cwdir=current_workdir),
                    shell=True)
                    
time.sleep(300)
subprocess.call("sudo reboot", shell=True)
# we copy content of rasp pi folder to server
# subprocess.call("sudo cp -r {cwdir} /mnt/Shared/Pictures/Raps_pi/PI4/{username}".format(username = user_name, cwdir = current_workdir), shell=True)
# subprocess.call("sudo cp -r {cwdir} /mnt/Shared/Pictures/Raps_pi/PI4/{username}".format(username = user_name, cwdir = current_workdir), shell=True)
