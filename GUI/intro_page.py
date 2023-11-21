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

# IR LEDs configuration
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(26, GPIO.OUT)  # inderpendent IR board # 37
GPIO.setup(23, GPIO.OUT)  # second IR board # 16


def get_ip():
    '''function to get and show IP in the status bar'''
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.1.1'
    finally:
        s.close()
    return IP


def show_values():
    # Showing values on sliders
    print(w1.get(), w2.get(), w3.get())


def color_switcher():
    global color_var
    color_var[-1] = white_choice.get()
    color_var[0] = red_choice.get()
    color_var[1] = green_choice.get()
    color_var[2] = blue_choice.get()


def streaming(timer=20000):
    colorWipe(strip, Color(0, 0, 0, 0), 0)
    colorWipe(strip, Color(50, 50, 50, 50), strip_length=[0, 22], step=3)
    subprocess.call("raspistill -t {}".format(timer), shell=True)
    # camera = picamera.PiCamera()
    # camera.resolution = '1280 x 720'
    # camera.start_preview()
    # time.sleep(timer)
    # camera.stop_preview()
    colorWipe(strip, Color(0, 0, 0, 0), 0)


def open_username(username_dict):
    ''' Keyboard and username/experiment name selection'''
    global username
    username[username_dict] = ""

    def press(num):
        global username
        username[username_dict] = username[username_dict] + str(num)
        equation.set(username[username_dict])

    # end

    # function clear button

    def clear():
        global username
        username[username_dict] = ""
        equation.set(username[username_dict])

    # end

    # closing window without closing the app
    def popup():
        global username
        if username[username_dict] == "":
            username[username_dict] = 'default_{}'.format(username_dict[:-5])
        p = tk.Toplevel(key)
        p.configure(bg='white')
        p.geometry('600x225+100+50')
        p_label = tk.Label(p, text='{} is set as the {}.'.format(username[username_dict], username_dict), bg='white')
        p_label.config(font=("Arial", 15, 'bold'))
        p_label.grid(row=0, column=0, ipadx=1, ipady=15)
        p.after(3000, key.destroy)


    key = tk.Toplevel(window)  # key window name
    key.title('Cool custom keyboard')  # title Name

    # Size window size
    key.geometry('800x450')  # normal size
    key.maxsize(width=800, height=450)  # maximum size
    key.minsize(width=800, height=450)  # minimum size
    # end window size

    key.configure(bg='grey')  # add background color

    # label
    username_label = tk.Label(key, text="Type your {} below:".format(username_dict), bg='grey')
    username_label.config(font=("Arial", 18, 'bold'), anchor="center")
    username_label.grid(rowspan=1, columnspan=7, column=0, ipadx=1, ipady=15)

    # entry box
    equation = tk.StringVar()
    Dis_entry = ttk.Entry(key, state='readonly', textvariable=equation)
    Dis_entry.grid(row=1, columnspan=300, ipadx=800, ipady=30)
    # end entry box

    # add all button line wise

    q = ttk.Button(key, text='Q', width=4, command=lambda: press('Q'))
    q.grid(row=3, column=0, ipadx=10, ipady=25)

    w = ttk.Button(key, text='W', width=4, command=lambda: press('W'))
    w.grid(row=3, column=1, ipadx=10, ipady=25)

    E = ttk.Button(key, text='E', width=4, command=lambda: press('E'))
    E.grid(row=3, column=2, ipadx=10, ipady=25)

    R = ttk.Button(key, text='R', width=4, command=lambda: press('R'))
    R.grid(row=3, column=3, ipadx=10, ipady=25)

    T = ttk.Button(key, text='T', width=4, command=lambda: press('T'))
    T.grid(row=3, column=4, ipadx=10, ipady=25)

    Y = ttk.Button(key, text='Y', width=4, command=lambda: press('Y'))
    Y.grid(row=3, column=5, ipadx=10, ipady=25)

    U = ttk.Button(key, text='U', width=4, command=lambda: press('U'))
    U.grid(row=3, column=6, ipadx=10, ipady=25)

    I = ttk.Button(key, text='I', width=4, command=lambda: press('I'))
    I.grid(row=3, column=7, ipadx=10, ipady=25)

    O = ttk.Button(key, text='O', width=4, command=lambda: press('O'))
    O.grid(row=3, column=8, ipadx=10, ipady=25)

    P = ttk.Button(key, text='P', width=4, command=lambda: press('P'))
    P.grid(row=3, column=9, ipadx=10, ipady=25)

    clear = ttk.Button(key, text='Clear', width=4, command=clear)
    clear.grid(row=2, column=10, ipadx=45, ipady=25)

    # Second Line Button

    A = ttk.Button(key, text='A', width=4, command=lambda: press('A'))
    A.grid(row=4, column=0, ipadx=10, ipady=25)

    S = ttk.Button(key, text='S', width=4, command=lambda: press('S'))
    S.grid(row=4, column=1, ipadx=10, ipady=25)

    D = ttk.Button(key, text='D', width=4, command=lambda: press('D'))
    D.grid(row=4, column=2, ipadx=10, ipady=25)

    F = ttk.Button(key, text='F', width=4, command=lambda: press('F'))
    F.grid(row=4, column=3, ipadx=10, ipady=25)

    G = ttk.Button(key, text='G', width=4, command=lambda: press('G'))
    G.grid(row=4, column=4, ipadx=10, ipady=25)

    H = ttk.Button(key, text='H', width=4, command=lambda: press('H'))
    H.grid(row=4, column=5, ipadx=10, ipady=25)

    J = ttk.Button(key, text='J', width=4, command=lambda: press('J'))
    J.grid(row=4, column=6, ipadx=10, ipady=25)

    K = ttk.Button(key, text='K', width=4, command=lambda: press('K'))
    K.grid(row=4, column=7, ipadx=10, ipady=25)

    L = ttk.Button(key, text='L', width=4, command=lambda: press('L'))
    L.grid(row=4, column=8, ipadx=10, ipady=25)

    Ⱈ = ttk.Button(key, text='Ⱈ', width=4, command=lambda: press('Ⱈ'))
    Ⱈ.grid(row=4, column=9, ipadx=10, ipady=25)

    enter = ttk.Button(key, text='Enter', width=4, command=popup)
    enter.grid(row=4, column=10, ipadx=45, ipady=25)

    # third line Button

    Z = ttk.Button(key, text='Z', width=4, command=lambda: press('Z'))
    Z.grid(row=5, column=0, ipadx=10, ipady=25)

    X = ttk.Button(key, text='X', width=4, command=lambda: press('X'))
    X.grid(row=5, column=1, ipadx=10, ipady=25)

    C = ttk.Button(key, text='C', width=4, command=lambda: press('C'))
    C.grid(row=5, column=2, ipadx=10, ipady=25)

    V = ttk.Button(key, text='V', width=4, command=lambda: press('V'))
    V.grid(row=5, column=3, ipadx=10, ipady=25)

    B = ttk.Button(key, text='B', width=4, command=lambda: press('B'))
    B.grid(row=5, column=4, ipadx=10, ipady=25)

    N = ttk.Button(key, text='N', width=4, command=lambda: press('N'))
    N.grid(row=5, column=5, ipadx=10, ipady=25)

    M = ttk.Button(key, text='M', width=4, command=lambda: press('M'))
    M.grid(row=5, column=6, ipadx=10, ipady=25)

    space = ttk.Button(key, text='_', width=4, command=lambda: press('_'))
    space.grid(row=5, columnspan=150, ipadx=145, ipady=25)

    # number row

    one = ttk.Button(key, text='1', width=4, command=lambda: press('1'))
    one.grid(row=2, column=0, ipadx=10, ipady=25)

    two = ttk.Button(key, text='2', width=4, command=lambda: press('2'))
    two.grid(row=2, column=1, ipadx=10, ipady=25)

    three = ttk.Button(key, text='3', width=4, command=lambda: press('3'))
    three.grid(row=2, column=2, ipadx=10, ipady=25)

    four = ttk.Button(key, text='4', width=4, command=lambda: press('4'))
    four.grid(row=2, column=3, ipadx=10, ipady=25)

    five = ttk.Button(key, text='5', width=4, command=lambda: press('5'))
    five.grid(row=2, column=4, ipadx=10, ipady=25)

    six = ttk.Button(key, text='6', width=4, command=lambda: press('6'))
    six.grid(row=2, column=5, ipadx=10, ipady=25)

    seven = ttk.Button(key, text='7', width=4, command=lambda: press('7'))
    seven.grid(row=2, column=6, ipadx=10, ipady=25)

    eight = ttk.Button(key, text='8', width=4, command=lambda: press('8'))
    eight.grid(row=2, column=7, ipadx=10, ipady=25)

    nine = ttk.Button(key, text='9', width=4, command=lambda: press('9'))
    nine.grid(row=2, column=8, ipadx=10, ipady=25)

    zero = ttk.Button(key, text='0', width=4, command=lambda: press('0'))
    zero.grid(row=2, column=9, ipadx=10, ipady=25)

    no_button = ttk.Button(key, text='Does nothing\n(enjoy pushing it)', width=4, command=lambda: press(''))
    no_button.grid(row=3, column=10, ipadx=45, ipady=18)


def warning_message(message):
    popup_mess = tk.Toplevel(window)
    popup_mess.configure(bg='white')
    popup_mess.geometry('600x225+100+50')
    popup_mess_label = tk.Label(popup_mess, text='No {} selected.'.format(message), bg='white')
    popup_mess_label.config(font=("Arial", 15, 'bold'))
    popup_mess_label.grid(row=0, column=0, ipadx=1, ipady=15)
    popup_mess.after(3000, popup_mess.destroy)


def launch():
    def meta_data_file_create(meta_loc='meta_data.py'):
        f = open(meta_loc, "w+")
        f.write("# Experiment info\r\n")
        f.close()
        f = open(meta_loc, "a+")
        f.write(f"total_experiment_length={total_experiment_length}\r\n")
        f.write(f"prelight_decision={prelight_decision}\r\n")
        f.write(f"apical_decision={apical_decision}\r\n")
        f.write(f"apical_hours={total_hours}\r\n")
        f.write(f"phototropic_hours={total_hours_light}\r\n")
        f.write(f"light_decision={light_decision}\r\n")
        f.write(f"light={[int(color[0]), int(color[1]), int(color[2]), int(color[3])]}\r\n")
        f.write(f"location='{os.getcwd()}'\r\n")
        f.write(f"main_process_PID={os.getpid()}\r\n")
        f.close()


    ''' masterfunction where all data recorded before should go into motion'''
    if ah_choice.get() == 0 and light_choice.get() == 0:
        warning_message('experiment')
    elif color_var == [0, 0, 0, 0]:
        warning_message('color settings')
    else:

        ''' Controlling part'''
        prelight_decision = pre_light.get() # checking if we want to have 6h pre illumination stage
        apical_decision = ah_choice.get()  # argument which turns off apical hook opening tracing (making pictures of it)
        total_hours = ah_value.get()  # how many hours before the light on (hours)
        period_min = freq_value.get()  # period between pictures (min)
        total_hours_light = light_value.get()  # for how long we want blue LEDs on (hours)
        total_experiment_length = total_hours + total_hours_light + prelight_decision*6 + 0.1

        # intensity of the light
        light_intensity = light_power.get()
        # color of the color strip
        light_decision = light_choice.get()

        # period_hours = round(period_min/60)
        total_minutes = total_hours * 60  # necessary for counting number of pictures		initial value is 60
        period_sec = period_min * 60  # (universal) sleep counts time in secs
        pic_num = int(total_minutes // period_min)  # takes only int part of quotient

        # unilateral light (blue)
        total_minutes_blue = total_hours_light * 60  # initial value is 60
        pic_num_blue = int(total_minutes_blue // period_min)
        color = list(np.array(color_var) * light_intensity)

# kill the process group
        
        # subprocess.call('python3 /home/pi/Camera/RaPiD-boxes-software/GUI/experiment_status.py >>/home/pi/Camera/RaPiD-boxes-software/GUI/output.txt 2>&1 &', shell=True)
        
        users_folders()
        meta_data_file_create()
        meta_data_file_create("/home/pi/Camera/RaPiD-boxes-software/GUI/meta_data.py")
        # create a process group
        e = subprocess.Popen(['python3', '/home/pi/Camera/RaPiD-boxes-software/GUI/experiment_status.py'], preexec_fn=os.setsid)
        time.sleep(3)
        # Initiating controlls
        c = subprocess.Popen(['python3', '/home/pi/Camera/RaPiD-boxes-software/GUI/controls.py'], preexec_fn=os.setsid)
        # init colored photo
        init_photo(int(color[0]), int(color[1]), int(color[2]), int(color[3]), 'init_photo')
        # Initial illumination
        initial_ill(prelight_decision)
        # starting AH cycle
        ah_cycle(pic_num, apical_decision, period_sec)
        # starting ph cycle (or not starting)
        bending_cycle(color, total_hours_light, light_decision, pic_num_blue, period_sec)
        # final white photo
        init_photo(0, 0, 0, 10, 'final_photo')
        # Processing
        # unfishing()
        # Mass suicide
        os.killpg(os.getpgid(e.pid), signal.SIGTERM)
        os.killpg(os.getpgid(c.pid), signal.SIGTERM)
        # open_popup(window)
        subprocess.call('sudo reboot', shell=True)
        sys.exit()
        



def initial_ill(prelight_decision, sleep_time=600):
    if prelight_decision == 1:
        for i in range(36):
            colorWipe(strip, Color(50, 50, 50, 50), strip_length=[22, 64])
            time.sleep(sleep_time)
        colorWipe(strip, Color(0, 0, 0, 0), 0)
    else:
        colorWipe(strip, Color(0, 0, 0, 0), 0)


def colorWipe(strip, palette, wait_ms=50, strip_length=[0, 64], step=1):
    """Updated color Wipe.
    Wipe color across display a pixel at a time"""
    for i in range(strip_length[0],strip_length[1], step):   # range of illuminated LEDs is defined
        strip.setPixelColor(i, palette)
        strip.show()
        time.sleep(wait_ms / 1000.0)


def users_folders():
    ''' creating experiment and user folder'''
    folder_name = str(datetime.date.today()).replace('-', '.') + '_PI3_' + '_' + username['experiment name']
    user_name = username['user name']
    os.chdir("/home/pi/camera/Experiments")

    if os.path.isdir("{}".format(user_name)):
        os.chdir("{}".format(user_name))
    else:
        os.mkdir("{}".format(user_name))
        os.chdir("{}".format(user_name))

    # that will release us from constant deleting of already existing folders
    if os.path.isdir("{}".format(folder_name)):
        folder_name = folder_name + "copy"
        os.mkdir("{}".format(folder_name))
        os.chdir("{}".format(folder_name))
    else:
        os.mkdir("{}".format(folder_name))
        os.chdir("{}".format(folder_name))


def init_photo(r, g, b, w, text):
    ''' Creating a single photo with given LED parameters'''
    colorWipe(strip, Color(r, g, b, w))  
    with picamera.PiCamera() as camera:
        camera.resolution = (3280, 2464)
        camera.framerate = 0.2
        camera.shutter_speed = 400000
        camera.exposure_mode = 'off'
        camera.iso = 100
        time.sleep(5)
        camera.capture('{}.jpg'.format(text))
    # Switching off LED strip
    colorWipe(strip, Color(0, 0, 0, 0), 0)


def ah_cycle(pic_num, apical_decision, period_sec):
    """ Running an apical hook stage/dark stage """
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(23, GPIO.OUT)  # IR left
    GPIO.setup(26, GPIO.OUT)  # IR right
    colorWipe(strip, Color(0, 0, 0, 0), 0)  # switch off light (just to be sure)
    # the cycle itself
    for i in range(pic_num):
        start_time = timeit.default_timer()  # when making of picture starts
        if int(apical_decision) == 1:  # by this, we disarm the whole cycle
            GPIO.output(23, GPIO.HIGH)
            GPIO.output(26, GPIO.HIGH)
            with picamera.PiCamera() as camera:
                camera.color_effects = (128, 128)  # b/w mode
                camera.resolution = (3280, 2464)
                camera.framerate = 0.2
                camera.shutter_speed = 800000  # exposure length, can be ajusted (max 6000000 - 6 sec)
                camera.exposure_mode = 'off'  # turning off of autoexposure
                camera.iso = 400
                # Give the camera a good long time to measure AWB
                # (you may wish to use fixed AWB instead)
                camera.awb_mode = 'off'
                camera.awb_gains = (Fraction(2), Fraction(1))
                time.sleep(5)
                camera.capture("./{}_cycle_{}h_dark.jpg".format(i, i*round(period_sec/3600, 2)))   # updated 2020.08.25
            GPIO.output(23, GPIO.LOW)
            GPIO.output(26, GPIO.LOW)
        # Making image of current state, while dark stage is ongoing
        else:
            # Image of current plant look
            GPIO.output(23, GPIO.HIGH)
            GPIO.output(26, GPIO.HIGH)
            with picamera.PiCamera() as camera:
                camera.color_effects = (128, 128)  # b/w mode
                camera.resolution = (3280, 2464)
                camera.framerate = 0.2
                camera.shutter_speed = 800000  # exposure length, can be ajusted (max 6000000 - 6 sec)
                camera.exposure_mode = 'off'  # turning off autoexposure
                camera.iso = 400
                # Give the camera a good long time to measure AWB
                # (you may wish to use fixed AWB instead)
                camera.awb_mode = 'off'
                camera.awb_gains = (Fraction(2), Fraction(1))
                time.sleep(5)
                camera.capture("./current_look_{}(dark_stage).jpg".format(i))
            GPIO.output(23, GPIO.LOW)
            GPIO.output(26, GPIO.LOW)
            # current photo should be only one
            exists = os.path.isfile("./current_look_{}(dark_stage).jpg".format(i-1))
            if exists:
                os.remove("./current_look_{}(dark_stage).jpg".format(i-1))
        elapsed = timeit.default_timer() - start_time
        time.sleep(float(period_sec) - elapsed)
    GPIO.cleanup()


def bending_cycle(color, total_hours_light, light_decision, pic_num_blue, period_sec):
    ''' Running a phototropic stage'''
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(23, GPIO.OUT)  # IR left
    GPIO.setup(26, GPIO.OUT)  # IR right

    # that will release us from constant deleting of already existing folders
    if total_hours_light != 0 or light_decision != 0:
        for i in range(pic_num_blue):
            start_time = timeit.default_timer()  # when making of picture starts
            colorWipe(strip, Color(0, 0, 0, 0), 0)  # switch off the light
            GPIO.output(23, GPIO.HIGH)
            GPIO.output(26, GPIO.HIGH)
            with picamera.PiCamera() as camera:
                camera.color_effects = (128, 128)  # b/w mode
                camera.resolution = (3280, 2464)
                camera.framerate = 0.2
                camera.shutter_speed = 800000  # exposure length, can be ajusted (max 6000000 - 6 sec)
                camera.exposure_mode = 'off'  # turning off of autoexposure
                camera.iso = 400
                # Give the camera a good long time to measure AWB
                # (you may wish to use fixed AWB instead)
                camera.awb_mode = 'off'
                camera.awb_gains = (Fraction(2), Fraction(1))
                time.sleep(5)
                camera.capture("./{}_{}_irradiated.jpg".format(i, color))
            if light_decision == 1:
                colorWipe(strip, Color(int(color[0]),int(color[1]), int(color[2]), int(color[3])), strip_length=[0, 21])
            elif light_decision == 2:
                colorWipe(strip, Color(int(color[0]),int(color[1]), int(color[2]), int(color[3])), strip_length=[22, 64])
            GPIO.output(23, GPIO.LOW)
            GPIO.output(26, GPIO.LOW)
            # adjustment of total time(cause it tends to run forward for ~45 sec per cycle
            elapsed = timeit.default_timer() - start_time
            time.sleep(float(period_sec) - elapsed)
        colorWipe(strip, Color(0, 0, 0, 0), 0)
    GPIO.cleanup()


def unfishing(distortion=-0.067):
    for file in os.listdir():
        f = os.path.join(os.getcwd(), file)
        if f[-3:] == 'jpg':
            imgobj = imageio.imread(f)
            output_img = fish(imgobj, distortion)
            imageio.imwrite(str(file[:-4] + '_processed.png'), output_img, format='png')

def open_popup(parent):
    # Create a popup window
    popup = tk.Toplevel(parent,bg="white")
    # Set the size of the popup window to be equal to that of the main window
    popup.geometry(parent.geometry())
    # Remove the outer frame of the popup window
    popup.overrideredirect(True)
    # Get the screen width and height in pixels
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    # Calculate the position of the popup window to be at the center of the screen
    position_x = (screen_width - 800) // 2
    position_y = (screen_height - 480) // 2
    # Move the popup window to that position
    popup.geometry(f"+{position_x}+{position_y}")
    # Create a label with a message
    # label = tk.Label(popup, text="Into measureless distances flying, Behind you, before, years extend…", bg="white")
    # label.pack()

    # Create a custom font object with a large size 
    huge_font = f.Font(family="Arial", size=20) 
    middle_font = f.Font(family="Arial", size=15) 
     
     # Create a label widget with huge letters and assign it to huge_font 
    message_label = tk.Label(popup, text="Your experiment is over. \n Please download your files ASAP,\n The storage is erazed automatically within a month\n \n Push the button to start over. ", font=middle_font, bg="white") 
     
     # Place message_label at center of pop up using pack() method 
    message_label.pack(anchor="n")
         # Create another button inside of pop up which will restart whole system (linux).
    restart_button=tk.Button(popup,text="Restart",command=restart_system, height=4,width=50,bg="red", font=huge_font)
    restart_button.pack(anchor="center")

# Define another function which will restart whole system (linux).
def restart_system():
   os.system("sudo reboot")

# Variables
username = {'experiment name': '', 'user name': ''}  # global variable

### This creates the main window of an application
window = tk.Tk()
SIGNATURE = socket.getfqdn() + " " + get_ip()
window.title("RaPiDBox v 3.1" + "  (" + SIGNATURE + ")")
window.geometry("800x450")
window.configure(background='white')

# defining font size
f = font.Font(size=14, family="Arial", weight="bold")

# # ### Navigation buttons
gridframe = tk.Frame(window)
gridframe.grid(row=1, column=0, columnspan=4,  ipady=0, ipadx=0, sticky='e')
close_butt = tk.Button(gridframe, text='Close',width = 13, font = f, height=2, bg='white', command=window.destroy).pack(side=tk.LEFT)
user_name = tk.Button(gridframe, text='User',width = 13, font = f, height=2, bg='white', command=lambda: open_username('user name')).pack(side=tk.LEFT)
exp_name = tk.Button(gridframe, text='Folder',width = 13, font = f, height=2, bg='white', command=lambda: open_username('experiment name')).pack(side=tk.LEFT)
focus = tk.Button(gridframe, text='Live',width = 13, font = f, height=2, bg='white', command=streaming).pack(side=tk.LEFT)
launch_button = tk.Button(gridframe, text='Launch',width = 13, font = f, height=2, bg='white', command=launch).pack(side=tk.LEFT)


### Checkboxes ###

pre_light = tk.IntVar()
ah_choice = tk.IntVar()
light_choice = tk.IntVar()

c1 = tk.Checkbutton(window, text='6h white light pre-treatment', width=26, variable=pre_light, onvalue=1, offvalue=0)
c1.grid(row=2, column=0, ipadx=25, ipady=15, columnspan=2)
c1.config(font=("Arial", 16, 'bold'), bg='white', anchor='w')

c2 = tk.Checkbutton(window, text='Dark stage', width=26, variable=ah_choice, onvalue=1, offvalue=0)
c2.grid(row=3, column=0, ipadx=25, ipady=15, columnspan=2)
c2.config(font=("Arial", 16, 'bold'), bg='white', anchor='w')

# Create two Radiobutton widgets with different values
c3 = tk.Radiobutton(window, text='Lateral light', width=13, variable=light_choice, value=1)
c3.grid(row=4, column=0, ipady=15, columnspan=1)
c3.config(font=("Arial", 16, 'bold'), bg='white', anchor='w')

c4 = tk.Radiobutton(window, text='Upright light', width=13, variable=light_choice, value=2)
c4.grid(row=4, column=1, ipady=15)
c4.config(font=("Arial", 16, 'bold'), bg='white', anchor='w')

### Sliders ###

ah_value = tk.IntVar()
light_value = tk.IntVar()
freq_value = tk.IntVar()
light_power = tk.IntVar()

# AH/Dark experiment length

AH_label = "Dark stage length (hours):"
w1 = tk.Scale(window, from_=0, to=350, width=26, tickinterval=50, label=AH_label, variable=ah_value,
              orient='horizontal')
w1.set(90)
w1.grid(row=5, column=0, ipadx=147, ipady=1, columnspan=2)
w1.config(font=("Arial", 12, 'bold'), bg='white')

# phototrop experiment length
PH_label = "Light stage length (hours):"
w2 = tk.Scale(window, from_=0, to=80, width=26, tickinterval=20, label=PH_label, variable=light_value, orient='horizontal')
w2.set(20)
w2.grid(row=6, column=0, ipadx=147, ipady=1, columnspan=2)
w2.config(font=("Arial", 12, 'bold'), bg='white')

# Frequency of imaging
freq_label = "Interval between images (minutes):"
w3 = tk.Scale(window, from_=10, to=240, width=26, tickinterval=30, label=freq_label, variable=freq_value,
              orient='horizontal')
w3.set(20)
w3.grid(row=5, columnspan=2, column=2, rowspan=1, ipadx=144, ipady=1, sticky='w')
w3.config(font=("Arial", 12, 'bold'), bg='white')

# Light intencity
light_power_label = "Light intensity:"
w4 = tk.Scale(window, from_=0, to=100, width=26, tickinterval=10, label=light_power_label, variable=light_power,
              orient='horizontal')
w4.set(10)
w4.grid(row=6, columnspan=2, column=2, rowspan=1, ipadx=144, ipady=1, sticky='w')
w4.config(font=("Arial", 12, 'bold'), bg='white')

''' Make possible to combine simultaneous buttons press + add clear button to unselect all'''

# Color label
color_label = tk.Label(window, text="Select LED color", bg='white')
color_label.config(font=("Arial", 18, 'bold'))
color_label.grid(row=2, column=2, columnspan=2, ipadx=10, ipady=15)

color_var = [0, 0, 0, 0]

# LED
white_choice = tk.IntVar()
red_choice = tk.IntVar()
green_choice = tk.IntVar()
blue_choice = tk.IntVar()

white = tk.Checkbutton(window, text="White", font=('Arial', 14, 'bold'), bg='white', width=5, variable=white_choice,
                       command=color_switcher, onvalue=1, offvalue=0)
white.grid(row=3, column=2, ipadx=55, ipady=15)

# Red LED
red = tk.Checkbutton(window, text="Red", font=('Arial', 14, 'bold'), bg='red', activebackground='pink', width=5,
                     variable=red_choice, command=color_switcher, onvalue=1, offvalue=0)
red.grid(row=3, column=3, ipadx=55, ipady=15)

# Green LED
green = tk.Checkbutton(window, text="Green", font=('Arial', 14, 'bold'), bg='#6df565',
                       activebackground='#c0f6bd', width=5,
                       variable=green_choice, command=color_switcher, onvalue=1, offvalue=0)
green.grid(row=4, column=2, ipadx=55, ipady=15)

# blue LED
blue = tk.Checkbutton(window, text="Blue", font=('Arial', 14, 'bold'),
                      activebackground='#bdc9f6', bg='#6c89fb', width=5,
                      variable=blue_choice, command=color_switcher, onvalue=1, offvalue=0)
blue.grid(row=4, column=3, ipadx=55, ipady=15)

tk.Button(window, text='Show', command=show_values)


window.mainloop()
