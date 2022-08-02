---
title: System updates to do on others pi's
updated: 2021-06-20 21:00:48Z
created: 2021-05-21 20:24:50Z
latitude: 50.07610000
longitude: 14.44790000
altitude: 0.0000
---

1.  Update packages:
    `sudo apt-get install python3-pil python3-pil.imagetk`
2.  Autohide top panel
3.  Remove mouse pointer from the top panel (unhides it automatically)
    `sudo nano /etc/xdg/lxsession/LXDE-pi/autostart` and comment `@point-rpi` line
4.  Select the lowest screen scaling
5.  `sudo apt-get install screen`
6.  [Upgrade Raspbian Stretch to Buster](../../../undefined)
7.  Enable automatic login
    Assuming your using the default username and password.

- From the command line enter 'sudo raspi-config'
- Select 'Boot Options', 'Desktop/CLI', 'Console Autologin'

<img src="../../../_resources/57752cc92a80cb84daf2986cabdf35e8.png" alt="57752cc92a80cb84daf2986cabdf35e8.png" width="360" height="235">

8. Install neopixel for python3:
`sudo pip3 install rpi_ws281x adafruit-circuitpython-neopixel`
`sudo python3 -m pip install --force-reinstall adafruit-blinka`
9. Install the module of screen brightness control
https://github.com/jakeh12/rpi-backlight
`rpi-backlight off`
`rpi-backlight min`
and set the screen brightness to minimum