import tkinter as tk
import os

# Create a main window
root = tk.Tk()
root.geometry("800x480")

# Create a button to open a popup window
button = tk.Button(root, text="Open Popup", command=lambda: open_popup(root))
button.pack()

# Define a function to open a popup window
def open_popup(parent):
    # Create a popup window
    popup = tk.Toplevel(parent)
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
    label = tk.Label(popup, text="This is a centered popup window")
    label.pack()
    # Create a button to close the popup window
    close_button = tk.Button(popup, text="Close", command=popup.destroy)
    close_button.pack()
    
     # Create another button inside of pop up which will restart whole system (linux).
    restart_button=tk.Button(popup,text="Restart",command=restart_system)
    restart_button.pack()

# Define another function which will restart whole system (linux).
def restart_system():
   os.system("sudo reboot")

# Start the main loop
root.mainloop()