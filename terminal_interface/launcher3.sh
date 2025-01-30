#!/bin/bash

#arguments order scriptname.py a x y z
# a - 1 as yes and 0 as no, argument which turns off apical hook opening tracing (making pictures of it)
# x - how many hours before Blue light on (hours)
# y - period between pictures (min) (universal throughout experiment)
# z - for how long we want blue LEDs on
# name - as data naming failed - we will ask user to name resulting folder
# user - lets sort content by users

#Mounting of disc
#sudo mount -t cifs //ds.asuch.cas.cz/ueb/lhr  /mnt/Shared -o user=LHR,pass=nMajF8 &> /dev/null 

#Introduction
echo " "
echo "*****Phenotyping PiBox v1.0*****"
echo " "

# a
echo "Do you want to measure apical hook opening dynamics? (yes/no)"
read ap_decision
if [[ $ap_decision == yes ]];then
    ap_decision=1
else
    ap_decision=0
fi

# x
if [ $ap_decision == 1 ];then
    echo "Length of apical hook opening experiment (number of hours)."
    read ap_time
else
    echo "Length of initial dark period (number in hours)."
    read ap_time
fi

# y period
echo "How often pictures should be shot? (period in minutes)"
read period

# z
echo "Do you want to measure hypocotyl bending dynamics? (yes/no)"
read bend_decision


if [ $bend_decision == yes ];then
    echo "Length of hypoctyl bending experiment (number of hours)."
    read bend_time
    bend_decision=1
    #color
    echo
    echo
    echo "Set color of unilateral light for phototropic experiment."
    echo "(red, green, blue, white(solo white diod +-3800k)," 
    echo "mixwhite(r+g+b, ~6000-7000k), brutalwhite (r+g+b+white)."
    read color
    # intensity
    echo
    echo
    echo "Set unilateral light intensity for phototropic experiment (0-255)."
    echo "Where 0 is darkness and 255 is dazzling light."
    echo "Default value is 10 and it's normally enough. Go up only if it's really necessary."
    echo "Never use 255. It can ignite the box :)"
    read light_intensity
else
    bend_time=0
    color="white"
    light_intensity=10
fi


# username
echo
echo "Introduce yourself please"
read username

# name
echo "Name the folder with your experiment."
read name

cd ~/camera
chmod +x Phototropism_program_3.py
sudo python Phototropism_program_3.py $ap_decision $ap_time $period $bend_time $name $username $color $light_intensity & 
echo $!" - This is your process PID. Type command 'sudo kill -9 "$!"', to terminate program if you need." > process_PID_inside.txt
echo $!" - This is your process PID. Type command 'sudo kill -9 PID', to terminate program if you need."

