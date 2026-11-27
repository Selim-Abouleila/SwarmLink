#!/usr/bin/env bash
set -euo pipefail
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
echo 'SwarmLink: starting the robot installer. Enter your sudo password if asked.'
sudo /usr/bin/python3 "$bundle_dir/setup_robot.py" --launch "$bundle_dir"
echo 'Setup is running on the robot and continues if SSH disconnects while Wi-Fi changes.'
echo 'Progress: cat /var/lib/swarmlink-setup/status.json'
echo 'Details: sudo journalctl -u swarmlink-setup -n 40 --no-pager'
