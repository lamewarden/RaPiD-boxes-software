# RaPiD-chambers-software
This setup is aimed to visualize gravity or light-stimulated movements of the plant seedlings. 
Raspberry PI3B+ was selected as a platform. For imaging Raspberry PiNoir v2 camera was used, coupled with 
12 IR (880nm) LED illumination to avoid undesirable light responce in plants. 
For the Lateral and top-down illumination adafruit NeoPixel RGBW LED stripes with 60 LEDs/m were used.
WaveShare 4.3' TFT resistive display was used for the grafical user interface operation.



## Content of the repository:
[1. General scheme](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/PI_boxes_scheme.pdf)

[2. Detailed scheme of wiring](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/pinout.pdf)

[3. Files for the headless control via ssh](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/terminal_interface)
- [Launcher file](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/terminal_interface/launcher3.sh)
- [Controlling Python script](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/terminal_interface/Phototropism_program_4.py)

[4. Files for GUI-based control](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/GUI);
- [External library to control NeoPixel RGB LEDs](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/GUI/rpi_ws281x-master);
- [Main program file](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/GUI/intro_page.py);
- [Experiment status window](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/GUI/experiment_status.py);
- [Control buttons](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/GUI/controls.py);

[5. Examples](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/examples)


## Operating manual
### Headless mode (over SSH connection)
1. Connect assembled RaPiD chamber to the local network (ideally over LAN cable);
2. Setup SSH connection between your working station and the RaPiD chamber - [official manual](https://www.raspberrypi.com/documentation/computers/remote-access.html);
3. Locate RaPiD-boxes_software folder to `/home/pi/camera`;
4. Make both files in the `RaPiD-boxes-software/terminal_interface/` folder executable by `sudo chmod +x <filename>`;
5. Run `launcher3.sh` file.

### GUI mode
1. Locate RaPiD-boxes_software folder to `/home/pi/camera`;
2. Make all `*.py` files in `RaPiD-boxes-software/GUI/` executable by `sudo chmod +x <filename>`;
3. Run `intro_page.py` file as root (unfortunatelly some libraries inside require superuser rights).






