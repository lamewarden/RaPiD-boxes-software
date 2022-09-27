# RaPiD-boxes-software
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

[4. Files for GUI-based control](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/GUI)

[5. Examples](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/examples)


## Operating manual
### Headless mode (over SSH connection)
1. Connect assembled RaPiD chamber to the local network (ideally over LAN cable);
2. Setup SSH connection between your working station and the RaPiD chamber - [official manual](https://www.raspberrypi.com/documentation/computers/remote-access.html)





