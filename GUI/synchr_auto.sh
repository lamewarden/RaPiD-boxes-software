#!/bin/bash

# Define the source and target directories
source_dir="/home/pi/camera/Experiments"
target_dir="/mnt/Shared/Users"

# Function to recursively scan directories and copy files
copy_files() {
    local source="$1"
    local target="$2"

    # Create the target directory if it doesn't exist
    mkdir -p "$target"

    # Iterate over all files and directories in the source directory
    for file in "$source"/*; do
        # If it's a directory, recursively call this function
        if [ -d "$file" ]; then
            copy_files "$file" "$target/$(basename "$file")"
        # If it's a file and it doesn't exist in the target directory, copy it
        elif [ -f "$file" ] && [ ! -f "$target/$(basename "$file")" ]; then
            cp "$file" "$target"
            echo "$file is copied to $target"
        fi
    done
}

# Continuously scan the source directory
while true; do
    copy_files "$source_dir" "$target_dir"
    echo "All files synchronized. Looking for changes."
    # Wait for 1 minute
    sleep 1000
done
