#!/bin/bash

# Define the directory to scan
dir="/home/pi/camera/Experiments"

# Use the find command to find files and directories older than two months and delete them
find "$dir" -mindepth 1 -mtime +60 -exec rm -rf {} \;
