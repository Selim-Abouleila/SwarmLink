#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/prepare-runtime.sh"
for attempt in {1..60}; do
    if [[ -r /dev/ttyACM0 && -w /dev/ttyACM0 && -r /dev/ttyUSB0 && -w /dev/ttyUSB0 ]]; then
        exec ros2 launch turtlebot3_bringup robot.launch.py
    fi
    sleep 1
done
echo 'Waiting for OpenCR and LiDAR USB devices; the service will retry.' >&2
exit 1
