#!/usr/bin/env bash
# Sourced by both services; intentionally does not enable shell tracing.
set -e
SWARMLINK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
set -a
source "$SWARMLINK_ROOT/.runtime/robot.env"
set +a
source "/opt/ros/${SWARMLINK_ROS_DISTRO:-jazzy}/setup.bash"
if [[ -f "$HOME/turtlebot3_ws/install/setup.bash" ]]; then
    source "$HOME/turtlebot3_ws/install/setup.bash"
fi
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-30}"
export TURTLEBOT3_MODEL="${TURTLEBOT3_MODEL:-burger}"
export LDS_MODEL="${LDS_MODEL:-LDS-01}"
export ROS_LOG_DIR="$SWARMLINK_ROOT/.runtime/ros-logs"
mkdir -p "$ROS_LOG_DIR"
cd "$SWARMLINK_ROOT"
