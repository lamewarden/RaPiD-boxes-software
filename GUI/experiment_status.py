#!/usr/bin/python3
import time
import os
import timeit
from meta_data import *
from tkinter import ttk
import tkinter as tk
from tkinter.messagebox import showinfo
from PIL import ImageTk, Image
import glob


# setting working directory
os.chdir('/home/pi/Camera/RaPiD-boxes-software/GUI')

def update_progress_label():
    return f"Current Progress: {round((((timeit.default_timer() - start_time)/3600)/total_experiment_length) * 100, 1)}%"


def update_time_left_label():
    return f"Experiment ends in: {round(total_experiment_length - (timeit.default_timer() - start_time)/3600 , 1)}h"


def show_file():
    list_of_files = glob.glob(location + '/*')  # * means all if need specific format then *.csv
    latest_file = max(list_of_files, key=os.path.getctime)
    image1 = Image.open(latest_file)
    # Reszie the image using resize() method
    resize_image = image1.resize((400, 380))
    img = ImageTk.PhotoImage(resize_image)
    label1 = tk.Label(image=img)
    label1.image = img
    label1.grid(column=4, row=1, rowspan =7, columnspan=3)


def meta_data_file_update(meta_loc='meta_data.py'):
        f = open(meta_loc, "a+")
        f.write(f"status_process_PID={os.getpid()}\r\n")
        f.close()

def loop_function():
    k = 0
    counter = 0
    while k <= MAX:
    ### some work to be done
        counter += 1
        progress_var.set(k)
        k = (((timeit.default_timer() - start_time)/3600)/total_experiment_length) * 100
        value_label['text'] = update_progress_label()
        time_left['text'] = update_time_left_label()
        time.sleep(1)
        pr_win.update_idletasks()
        if counter % 3600 == 0 or counter == 20:
            show_file()
    pr_win.after(100, showinfo(message='The experiment is finished!'))


# experiment start time
start_time = timeit.default_timer()
proccess_pid = os.getpid()


""" Creating a window with a progress bar and info of the experiment stage"""
pr_win = tk.Tk()
pr_win.geometry('800x450')
# Label
progress_label = tk.Label(pr_win, text="Experiment is running")
progress_label.config(font=("Arial", 18, 'bold'), anchor="center")
progress_label.grid(rowspan=1, columnspan=2, column=0, ipadx=1, ipady=15)


# Total progress bar
MAX = 102
progress_var = tk.DoubleVar() #here you have ints but when calc. %'s usually floats
theLabel = tk.Label(pr_win, text="Sample text to show")
progressbar = ttk.Progressbar(pr_win, variable=progress_var,
                              maximum=MAX, orient='horizontal',
                              mode='determinate', length=370)
progressbar.grid(row=2, column=0, columnspan=2, ipadx=10, ipady=10)


# total progress %
value_label = ttk.Label(pr_win, text=update_progress_label())
value_label.grid(column=0, row=3)
# Total time left
time_left = ttk.Label(pr_win, text=update_time_left_label())
time_left.grid(column=1, row=3)

# Essay parameters:
exp_info = tk.Label(pr_win, text="Experiment parameters:")
exp_info.config(font=("Arial", 18, 'bold'), anchor="w")
exp_info.grid(row=5, columnspan=2, column=0, ipadx=1, ipady=15)

# Assay type




string = ""
if prelight_decision == 1:
    string += f"PreLight 6h"
if apical_decision == 1:
    string += f" Ap.hook {round(apical_hours,1)}h \r\n"
if light_decision == 1:
    string += f" Lat.light {round(phototropic_hours, 1)}h"
if light_decision == 2:
    string += f" Up.light {round(phototropic_hours, 1)}h"


ap_h_info = tk.Label(pr_win, text=string)
ap_h_info.config(font=("Arial", 16, 'bold'), anchor="w")
ap_h_info.grid(row=5, columnspan=2, column=0, ipadx=10, ipady=130,  rowspan =2)

# and light_decision == 1:
#     ap_h_info = tk.Label(pr_win, text=f"Ap.hook {round(apical_hours,1)}h + phototropism {round(phototropic_hours, 1)}h \r\nprocessing {round(processing_hours,1)}h")
#     ap_h_info.config(font=("Arial", 16, 'bold'), anchor="w")
#     ap_h_info.grid(row=5, columnspan=2, column=0, ipadx=10, ipady=130,  rowspan =2)
# elif apical_decision == 0 and light_decision == 1:
#     ap_h_info = tk.Label(pr_win, text=f"Phototropism {round(phototropic_hours, 1)}h + processing{round(processing_hours,1)}h")
#     ap_h_info.config(font=("Arial", 16, 'bold'), anchor="w")
#     ap_h_info.grid(row=5, columnspan=2, column=0, ipadx=10, ipady=130, rowspan =2)
# elif apical_decision == 1 and light_decision == 0:
#     ap_h_info = tk.Label(pr_win, text=f"Apical hook development {apical_hours}h + processing{round(processing_hours,1)}h")
#     ap_h_info.config(font=("Arial", 16, 'bold'), anchor="w")
#     ap_h_info.grid(row=5, columnspan=2, column=0, ipadx=10, ipady=130, rowspan =2)
# elif apical_decision == 0 and light_decision == 2:

# light
light_info = tk.Label(pr_win, text=f"Light settings: {light}")
light_info.config(font=("Arial", 14, 'bold'), anchor="w")
light_info.grid(row=6, columnspan=2, column=0, ipadx=1, ipady=15)

# location of the experiment
loc_info = tk.Label(pr_win, text="Assay is running. Go home")
loc_info.config(font=("Arial", 14, 'bold'), anchor="w")
loc_info.grid(row=6, columnspan=2, column=0, ipadx=1, ipady=15)


# Updating meta-data
meta_data_file_update()


# subprocess.call('python3 /home/pi/Camera/RaPiD-boxes-software/GUI/controls.py >>/home/pi/Camera/RaPiD-boxes-software/GUI/output.txt 2>&1 &', shell=True)

# refreshing the screen
loop_function()



pr_win.mainloop()
