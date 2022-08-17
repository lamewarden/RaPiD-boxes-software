#!/usr/bin/python3
import time
import os
import datetime
import timeit
from meta_data import *
from tkinter import ttk
import tkinter as tk
from tkinter.messagebox import showinfo
from PIL import ImageTk, Image
import glob


def update_progress_label():
    return f"Current Progress: {round((((timeit.default_timer() - start_time)/3600)/total_experiment_length) * 100, 3)}%"


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
        print(counter)
        if counter % 3600 == 0 or counter == 20:
            show_file()
    pr_win.after(100, showinfo(message='The progress completed!'))

# waiting till the initial picture is made
# time.sleep(20)

# experiment start time
start_time = timeit.default_timer()


""" Creating a window with a progress bar and info of the experiment stage"""
pr_win = tk.Tk()
pr_win.geometry('800x450')
# Label
progress_label = tk.Label(pr_win, text="Experiment is running")
progress_label.config(font=("Arial", 18, 'bold'), anchor="center")
progress_label.grid(rowspan=1, columnspan=2, column=0, ipadx=1, ipady=15)
# Buttons
# Kill the app
kill_butt = tk.Button(pr_win, text="Close", width=16, bg='white', command=pr_win.destroy)
kill_butt.grid(row=1, column=0, ipadx=5, ipady=5, sticky='w')
kill_butt.config(font=("Arial", 14, 'bold'), bg='white')
# reboot hte system
restart_butt = tk.Button(pr_win, text="Reboot", width=16, bg='white', command=lambda: os.system('reboot now'))
restart_butt.grid(row=1, column=1, ipadx=5, ipady=5, sticky='w')
restart_butt.config(font=("Arial", 14, 'bold'), bg='white')

# Total progress bar
MAX = 30
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
if apical_decision == 1 and ph_decision == 1:
    ap_h_info = tk.Label(pr_win, text=f"Apical hook {apical_hours}h + phototropism {total_experiment_length-apical_hours}h")
    ap_h_info.config(font=("Arial", 16, 'bold'), anchor="w")
    ap_h_info.grid(row=5, columnspan=2, column=0, ipadx=10, ipady=130,  rowspan =2)
elif apical_decision == 0 and ph_decision == 1:
    ap_h_info = tk.Label(pr_win, text=f"Phototropism {total_experiment_length-apical_hours}h")
    ap_h_info.config(font=("Arial", 16, 'bold'), anchor="w")
    ap_h_info.grid(row=5, columnspan=2, column=0, ipadx=10, ipady=130, rowspan =2)
elif apical_decision == 1 and ph_decision == 2:
    ap_h_info = tk.Label(pr_win, text=f"Apical hook development {apical_hours}h")
    ap_h_info.config(font=("Arial", 16, 'bold'), anchor="w")
    ap_h_info.grid(row=5, columnspan=2, column=0, ipadx=10, ipady=130, rowspan =2)

# light
light_info = tk.Label(pr_win, text=f"Light settings: {light}")
light_info.config(font=("Arial", 14, 'bold'), anchor="w")
light_info.grid(row=6, columnspan=2, column=0, ipadx=1, ipady=15)

# location of the experiment
loc_info = tk.Label(pr_win, text="Assay is running. Go home")
loc_info.config(font=("Arial", 14, 'bold'), anchor="w")
loc_info.grid(row=6, columnspan=2, column=0, ipadx=1, ipady=15)





loop_function()
pr_win.mainloop()