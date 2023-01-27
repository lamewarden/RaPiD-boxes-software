# RaPiD-chambers-software
Original publication - 
[RaPiD-chamber: Easy to self-assemble live-imaging chamber with adjustable LEDs allows to track small differences in dynamic plant movement adaptation on tissue level](https://www.biorxiv.org/content/10.1101/2022.08.13.503848v1)

This project is designed to show how plants move in response to gravity or light. To accomplish this, a Raspberry PI3B+ computer and a Raspberry PiNoir v2 camera which is IR-sensitive were used. Illumination is set up with 12 infrared (IR) lights to prevent unwanted reactions from the plants. To create light stimuli for dark grown plants, Adafruit NeoPixel RGBW LED stripes with 60 LEDs per meter were used for both lateral and top-down illumination. The project also uses a WaveShare 4.3-inch TFT resistive display to create a graphical user interface for operation.

## Contacts:
For support, address Ivan Kashkan <kashkan.van@gmail.com>/<kashkan@ueb.cas.cz>



## Content of the repository:
[1. General scheme](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/PI_boxes_scheme.pdf)

[2. Detailed scheme of wiring](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/pinout.pdf)

[3. Files for the headless control via ssh](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/terminal_interface)
- [Launcher file](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/terminal_interface/launcher3.sh)
- [Controlling Python script](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/terminal_interface/Phototropism_program_4.py)

[4. Files for GUI-based control](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/GUI);
- [External library to control NeoPixel RGB LEDs](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/GUI/rpi_ws281x-master), written by [richardghirst](https://github.com/richardghirst);
- [Main program file](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/GUI/intro_page.py);
- [Experiment status window](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/GUI/experiment_status.py);
- [Control buttons](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/GUI/controls.py);

[5. Examples](https://github.com/lamewarden/RaPiD-boxes-software/tree/main/examples)

[6. Operating manual](https://github.com/lamewarden/RaPiD-boxes-software/blob/main/Manual.pdf)


## Initial setup:
1.  Update packages:
    `sudo apt-get install python3-pil python3-pil.imagetk`
2.  Autohide top panel
3.  Remove mouse pointer from the top panel (unhides it automatically)
    `sudo nano /etc/xdg/lxsession/LXDE-pi/autostart` and comment `@point-rpi` line
4.  Select the lowest screen scaling
5.  `sudo apt-get install screen`
6.  (_if needed_)[Upgrade Raspbian Stretch to Buster or Bullseye](https://www.tomshardware.com/how-to/upgrade-raspberry-pi-os-to-bullseye-from-buster)
7.  Enable automatic login
    Assuming your using the default username and password.

- From the command line enter `sudo raspi-config`
- Select `Boot Options`, `Desktop/CLI`, `Console Autologin`

8. Install neopixel for python3:
`sudo pip3 install rpi_ws281x adafruit-circuitpython-neopixel`
`sudo python3 -m pip install --force-reinstall adafruit-blinka`
9. Install the module of screen brightness control
https://github.com/jakeh12/rpi-backlight
`rpi-backlight off`
`rpi-backlight min`
and set the screen brightness to minimum
### Headless mode (over SSH connection)
1. Connect assembled RaPiD chamber to the local network (ideally over LAN cable);
2. Setup SSH connection between your working station and the RaPiD chamber - [official manual](https://www.raspberrypi.com/documentation/computers/remote-access.html);
3. Locate RaPiD-boxes_software folder to `/home/pi/camera`;
4. Make both files in the `RaPiD-boxes-software/terminal_interface/` folder executable by `sudo chmod +x <filename>`;
5. Run `launcher3.sh` file.

### GUI mode
1. Locate RaPiD-boxes_software folder to `/home/pi/camera`;
2. Make all `*.py` files in `RaPiD-boxes-software/GUI/` executable by `sudo chmod +x <filename>`;
3. Run `intro_page.py` file as root (unfortunatelly some libraries require superuser rights).






