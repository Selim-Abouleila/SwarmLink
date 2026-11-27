# Document 07 - Set up a new robot without a chat

Open **[SETUP NEW ROBOT.cmd](../data-engineering/SETUP%20NEW%20ROBOT.cmd)** from the `data-engineering` folder on the laptop. It opens a guided terminal window. You can use it yourself for the next robot; you do not need to ask an assistant to run the setup.

This version supports TurtleBot3 robots on **64-bit Ubuntu 24.04 with ROS 2 Jazzy**. It can install missing ROS packages. The Ubuntu installation, Linux login account, OpenCR firmware, and physical wiring must already be working. It does not install an operating system or flash firmware.

## What you need first

- The laptop already able to run the SwarmLink dashboard, with Python installed.
- The robot's Linux username and password. The account must be able to use `sudo` to install packages.
- The hotspot name and password, if the robot needs a new Wi-Fi connection.
- The robot's TurtleBot3 model and LiDAR model. Our current robot is a Burger with an LDS-01 and uses the `percent` battery setting.
- Internet access on the robot during package installation. Later, local telemetry does not need internet.

Keep the robot powered throughout the installation. A first package installation may take longer than 20 minutes on a slow connection.

The laptop needs an initial way to reach the robot before it can configure Wi-Fi. Choose one of the two paths below.

## Option 1: the robot is reachable over Ethernet or Wi-Fi

1. Double-click **SETUP NEW ROBOT.cmd** and choose **1**.
2. Enter the robot's username and current IP address or hostname. SSH must already be enabled. Ethernet needs a working address; plugging in a cable alone does not guarantee that.
3. Enter the robot login password when SSH asks. On the first connection, SSH also asks you to verify the robot's fingerprint. Password characters are hidden while typing.
4. Accept the suggested unused robot ID, such as `robot-02`. Confirm its hardware model, LiDAR model, and battery scale. Keep `hotspot-live` as the experiment name to see it with the current robot.
5. Enter the exact hotspot name and its password. Leave the hotspot name blank if the robot is already on the right network.
6. Review the short setup summary and enter **yes**. The script transfers the package and starts installation. SSH/SCP may ask for the login password again, and `sudo` asks for the robot's administrator password.
7. Leave the launcher open. It opens the dashboard and waits for the new robot's readings.

SSH and SCP are included as the OpenSSH Client on many Windows installations. If the launcher says they are missing, enable **OpenSSH Client** in Windows Optional features, or use the USB path.

The installer runs as a background job on the robot. If switching Wi-Fi disconnects SSH, the installation continues. An interrupted connection is not proof that setup failed; use **Check a setup** before starting another job.

## Option 2: the robot is not on any network yet

1. Double-click **SETUP NEW ROBOT.cmd** and choose **2**.
2. Enter the robot username, choose its unused ID and hardware settings, and enter the hotspot details.
3. The launcher creates a ZIP under `.runtime/setup/`. Copy that ZIP to a USB drive and extract it.
4. Plug the USB drive into the robot. Using its screen and keyboard, log in and open a terminal in the extracted setup folder.
5. Run this one command and enter the robot's `sudo` password:

   ```bash
   bash SETUP-ROBOT.sh
   ```

6. Return to the laptop's setup window and press Enter. It checks whether the robot reaches the dashboard.

The robot script saves the hotspot connection, connects to Wi-Fi, and downloads the required packages. The USB path does not require SSH for the initial installation.

Use each USB package for its assigned physical robot only. It contains that robot's access key and the hotspot password. Keep it private and remove the USB copy after setup. Do not commit, email, or share the package with the project repository.

## What the script handles

| Step | What happens |
| --- | --- |
| Identity | Suggests an unused provisioned robot ID and checks existing assignments. Each package receives only that robot's key. |
| Sensor isolation | Assigns a separate ROS domain: robot-01 uses 30, robot-02 uses 31, and so on. |
| Wi-Fi | Saves a WPA personal hotspot profile for automatic connection. Supports NetworkManager and ordinary DHCP netplan configurations. |
| Packages | Installs ROS 2 Jazzy base/runtime packages, TurtleBot3 bringup, and the selected LDS driver through apt. Adds the official ROS apt source if needed. |
| Agent | Installs the SwarmLink agent and its private configuration under the robot user's `~/SwarmLink/data-engineering`. |
| Startup | Enables the sensor and agent user services and user lingering so they can start without an interactive login. |
| Verification | Starts or reuses the laptop dashboard, then checks for newly received odometry, LiDAR, and battery data from the chosen robot. |

The script does not send movement commands. It uses the normal TurtleBot sensor bringup. Stop any separate, manually launched bringup before using the installer so two driver instances do not compete for the same devices.

The current collector already has keys for robots 01–04. The launcher preserves existing keys. On a fresh laptop installation it creates four keys once. If all provisioned IDs are used, it asks for more collector keys rather than silently reusing another robot's ID.

The separate ROS domains keep this version's sensor readings apart. If the team's swarm software needs a shared ROS domain, use per-robot topic namespaces and configure the agents accordingly; that is a separate integration step.

## How to read the result

- **SUCCESS:** the dashboard received fresh odometry, LiDAR, and battery readings from the robot.
- **CONNECTED:** its agent reached the dashboard, but the message lists sensors that still need checking.
- **Not verified yet:** installation may still be running, or a step failed. Open the launcher again and choose **3 - Check a setup already started**.

The check waits up to 20 minutes. Closing the laptop window or pressing Ctrl+C stops that wait, but does not cancel an installer already running on the robot. Package installation can continue after the wait ends.

To check locally on the robot, run:

```bash
cat /var/lib/swarmlink-setup/status.json
sudo journalctl -u swarmlink-setup -n 40 --no-pager
```

The status gives the last step: `wifi`, `packages`, `services`, `complete`, or `failed`. `complete` means installation finished; the laptop's live-data check is the separate sensor verification.

## If something goes wrong

| Message or symptom | What to do |
| --- | --- |
| Cannot connect by SSH | Check the current IP, username, SSH service, and network connection. If there is no initial connection, use USB. |
| Login or sudo fails | Use the credentials set on that robot. They are not necessarily the same as Robot 01. |
| Wi-Fi failed | Check the exact hotspot name, password, and signal. Failed connection attempts restore the previous managed network configuration where possible. |
| Package step failed | Check that the robot has internet access. The setup log shows the apt error. Then rerun the launcher for the same robot. |
| Custom static Wi-Fi configuration | Connect that network locally first, then rerun with the hotspot field blank. The installer does not replace a custom static Wi-Fi setup. |
| Robot ID already used | Choose a genuinely unused ID for a new physical robot. For a retry, explicitly confirm the same physical robot when asked. |
| SSH connection dropped during setup | Check setup status using its new Wi-Fi IP. On its local terminal, `hostname -I` shows its addresses. |
| Connected, sensors missing | Check OpenCR/LiDAR power and USB cables, the selected LDS model, and the sensor service log. |
| Installer complete but no dashboard data | Keep the laptop and robot on the same reachable network. Check TCP 8765, UDP 8766, matching keys/run names, and hotspot client isolation. |

For the sensor and agent logs on the robot:

```bash
systemctl --user status swarmlink-bringup swarmlink-agent --no-pager
journalctl --user -u swarmlink-agent -u swarmlink-bringup -n 40 --no-pager
```

A known SSH host key mismatch stops the SSH path. Verify the physical robot before updating an old saved host entry; the launcher does not disable that check.

SSH retries recognise the same machine identity. An unfinished USB setup can be rebuilt with its reserved ID after you confirm it is for the same robot. Existing runtime files are backed up inside the root-owned setup job directory before replacement. Netplan changes keep original files there too.

## Files behind the launcher

All implementation paths below start inside `data-engineering/`.

| File | Purpose |
| --- | --- |
| `SETUP NEW ROBOT.cmd` | Opens the guided setup on Windows. |
| `scripts/setup-new-robot.py` | Starts the Python wizard. |
| `swarmlink_data/provisioning.py` | Handles questions, IDs, private packages, SSH/USB transfer, and dashboard verification. |
| `scripts/package-robot.py` | Builds the package containing only the assigned robot's key and runtime files. |
| `deployment/robot/SETUP-ROBOT.sh` | Starts installation on the robot with sudo. |
| `deployment/robot/setup_robot.py` | Configures Wi-Fi, installs packages, and enables startup services. |

SSH passwords are handled directly by OpenSSH and are not saved by the wizard. Generated credentials, packages, and enrollment records stay under ignored `.runtime/` storage. The Wi-Fi password must remain in the robot's protected network profile for automatic connection.

## What has been checked

The Windows entry point, bundle contents, input handling, duplicate-ID checks, Wi-Fi rollback, background-job launch, and live-data success conditions have automated checks. The full installer has **not yet been run on a second physical robot**. Test one new robot first, then repeat for the others. A real reboot is also needed to confirm startup on each physical unit.

The installation choices follow the [ROS 2 Jazzy apt instructions](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html), [ROBOTIS SBC setup guide](https://docs.robotis.com/docs/systems/turtlebot3/quick_start_guide/sbc_setup/), [NetworkManager keyfile format](https://networkmanager.dev/docs/api/latest/nm-settings-keyfile.html), and [Netplan Wi-Fi examples](https://netplan.readthedocs.io/en/stable/examples/).
