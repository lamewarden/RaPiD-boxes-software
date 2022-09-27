#!/usr/bin/python3
import os
from meta_data import *
import tkinter as tk
import signal
from meta_data import *
import subprocess

# setting working directory
os.chdir('/home/pi/Camera/RaPiD-boxes-software/GUI')

def murderer():
    os.kill(main_process_PID, signal.SIGTERM)
    os.kill(status_process_PID, signal.SIGTERM)
    subprocess.call('sudo python3 /home/pi/Camera/RaPiD-boxes-software/GUI/intro_page.py &', shell=True)
    os.kill(os.getpid(), signal.SIGTERM)

popup_butts = tk.Tk()
popup_butts.title("controls")
popup_butts.geometry('350x40+15+200')

# Buttons
# Kill the app
kill_butt = tk.Button(popup_butts, text="Close", width=16, bg='white', command=murderer)
kill_butt.grid(row=1, column=0, ipadx=5, ipady=5, sticky='w')
kill_butt.config(font=("Arial", 12, 'bold'), bg='white')
# reboot hte system
restart_butt = tk.Button(popup_butts, text="Reboot", width=16, bg='white', command=lambda: os.system('reboot now'))
restart_butt.grid(row=1, column=1, ipadx=5, ipady=5, sticky='w')
restart_butt.config(font=("Arial", 12, 'bold'), bg='white')
popup_butts.attributes('-topmost', True)
# popup_butts.lift()


popup_butts.mainloop()