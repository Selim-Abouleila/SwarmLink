# Document 07 - Set up a new robot

Double-click **[SETUP NEW ROBOT.cmd](../data-engineering/SETUP%20NEW%20ROBOT.cmd)** in the laptop's `data-engineering` folder. Answer the questions. The script installs the robot software and checks for live data on the dashboard. You can use it without starting another chat.

The script supports **TurtleBot3 with 64-bit Ubuntu 24.04 and ROS 2 Jazzy**. It can install missing ROS packages. Ubuntu, the Linux account, the OpenCR firmware, and the robot's wiring must already work.

## What you need

- A Windows laptop that can already run the SwarmLink dashboard.
- The new robot's Linux username and password. The account needs `sudo`, which allows package installation.
- The hotspot name and password, if the robot needs to join Wi-Fi.
- The TurtleBot model and the LiDAR model printed on the sensor. Our current robot is a Burger with an LDS-01.
- Internet access on the robot to download packages. Local dashboard data does not need internet after installation.

Keep the robot powered during setup. Downloading packages can take more than 20 minutes on a slow connection.

## Choose how to reach the robot

| What you have | Launcher option | What must work first |
| --- | --- | --- |
| Ethernet cable, directly to the laptop or through a router/switch | **1 - Set up over SSH** | The laptop can reach the robot's Ethernet IP, and SSH is enabled. |
| Robot already on a reachable Wi-Fi network | **1 - Set up over SSH** | The laptop can reach the robot's Wi-Fi IP, and SSH is enabled. |
| No working network connection | **2 - Prepare USB setup** | A USB drive, plus a screen and keyboard on the robot. |
| Setup started, but you want its progress or error log | **3 - Check a setup already started** | SSH access to the robot's current IP. |

SSH opens a terminal on the robot over the network. SCP copies the setup files over that same connection. Windows needs the OpenSSH Client for options 1 and 3. If it is missing, enable it in Windows Optional features or use option 2.

## Before setup over Ethernet

**Yes, a direct laptop-to-robot cable works.** It needs working IP addresses first. The launcher does not assign the initial Ethernet addresses or set up laptop internet sharing.

With a router, DHCP can give both devices addresses automatically. A switch by itself does not provide DHCP. Check the robot's addresses on its own terminal:

```bash
ip -br addr
```

Look at the Ethernet interface, often `eth0`. If it has no IPv4 address, prepare that connection before choosing option 1. Do not use the old IP written on a box unless it is still assigned to this robot.

For a direct cable with no working IPv4 setup, this is one example:

| Device | Ethernet IPv4 address | Subnet mask / prefix |
| --- | --- | --- |
| Laptop | `192.168.50.1` | `255.255.255.0` / `24` |
| Robot | `192.168.50.2` | `255.255.255.0` / `24` |

Use this example only if `192.168.50.0/24` is not already used by your Wi-Fi, VPN, or another network. Set the laptop's **Ethernet adapter** to the address above in Windows network settings. Leave its gateway and DNS blank for this direct link. Keep the laptop's Wi-Fi connected to the target hotspot.

On the robot's own terminal, replace `eth0` if its Ethernet interface has another name:

```bash
sudo ip link set eth0 up
sudo ip addr add 192.168.50.2/24 dev eth0
ip -br addr show eth0
```

This adds a temporary robot address. It is lost after a reboot and may be replaced by the network manager. For a permanent address, use the robot's network settings. See [Ubuntu's IP configuration guide](https://ubuntu.com/server/docs/explanation/networking/configuring-networks/).

If SSH is missing, install it on the robot while it has internet access:

```bash
sudo apt update
sudo apt install openssh-server
sudo systemctl enable --now ssh
```

This uses Ubuntu's [OpenSSH server](https://ubuntu.com/server/docs/how-to/security/openssh-server/). You can use the USB setup path instead if you cannot prepare SSH yet.

From PowerShell on the laptop, test the login. Replace the username and address with the robot's real values:

```powershell
ssh turtle@192.168.50.2
```

If the login works, type `exit` and start the launcher. A direct Ethernet cable does not give the robot internet by itself. The installer can connect the robot to your hotspot before downloading packages.

## Option 1: install over Ethernet or existing Wi-Fi

1. Double-click **SETUP NEW ROBOT.cmd** and choose **1**.
2. Enter the robot's Linux username and current IP address. Use its Ethernet IP when setting up through the cable.
3. Enter the robot password when SSH asks. On the first connection, check the host fingerprint before accepting it. Password characters stay hidden while you type.
4. Accept the suggested unused robot ID, such as `robot-02`. Check the TurtleBot model, LiDAR model, and battery scale. Our current Burger uses `percent`; another publisher may use `fraction`.
5. Keep `hotspot-live` as the experiment name to group it with the current robot.
6. Enter the target hotspot name and password. Leave the name blank to keep the robot's current network settings.
7. Check the setup summary and enter **yes**. Enter the login or `sudo` password again if asked.
8. Leave the launcher open. It starts or opens the dashboard and waits for real sensor data.

The installer runs in the background on the robot. It keeps running if SSH disconnects during the Wi-Fi change. Use option 3 to check progress before starting another setup attempt.

After setup, check that readings still update with the Ethernet cable unplugged if you plan to use Wi-Fi. Both the laptop and robot must be on a network where they can reach each other.

## Option 2: install with a USB drive

1. Double-click **SETUP NEW ROBOT.cmd** and choose **2**.
2. Enter the robot username, unused robot ID, hardware settings, and hotspot details.
3. Copy the ZIP shown by the launcher to a USB drive. Extract it.
4. Plug the drive into the robot. Log in using its screen and keyboard. Open a terminal in the extracted setup folder.
5. Run this command and enter the robot's `sudo` password:

   ```bash
   bash SETUP-ROBOT.sh
   ```

6. Return to the laptop's setup window and press Enter. It checks for the robot on the dashboard.

The script connects Wi-Fi first, then downloads and installs the packages. This path does not need SSH for installation. You can read setup status on the robot's own terminal.

Use each USB package for one assigned robot only. It contains that robot's key and the hotspot password. Keep it private and remove the USB copy after setup.

## What the script sets up

| Part | What it does |
| --- | --- |
| Robot ID and key | Selects an unused ID and copies only its matching access key. The collector uses the key to accept this robot's data. |
| ROS domain | Keeps each robot's sensor topics separate: robot-01 uses 30, robot-02 uses 31, and so on. |
| Wi-Fi | Saves the hotspot for automatic connection. Supports NetworkManager and normal DHCP setups with Netplan. |
| Packages | Installs ROS 2 Jazzy, TurtleBot3 sensor startup packages, and the selected LiDAR driver. Uses the official ROS package source. |
| Agent | Installs the Python program that reads the robot's sensor topics and sends data to the laptop. |
| Startup services | Enables `swarmlink-bringup` for sensors and `swarmlink-agent` for data. Enables user lingering so they can start without a login. |
| Data check | Looks for fresh odometry, LiDAR, and battery readings from the selected robot. |

Stop any sensor startup process you launched manually before installation. Two copies can compete for the same USB devices. The setup script sends no movement commands.

The collector has keys for robots 01-04. The launcher keeps existing keys. On a fresh laptop it creates four keys once. If all IDs are used, more collector keys must be added before another robot can be set up.

The robot finds the collector through **UDP 8766** and sends data through **TCP 8765**. SSH setup uses **TCP 22**. The laptop firewall and network must allow the required connections. A hotspot or guest network that blocks communication between devices can prevent data from arriving.

After setup, the agent can find the dashboard automatically whenever both are on a reachable local network. Joining Wi-Fi alone is not enough for a robot that has never been set up. A new Wi-Fi name/password must also be saved on each robot before it can join that network.

Separate ROS domains suit this sensor setup. If the team's swarm software needs one shared domain, give each robot separate topic names and match its agent settings. See the manual steps in [Document 03](03-robot-setup.md).

## Read the result

- **SUCCESS:** fresh odometry, LiDAR, and battery readings reached the dashboard.
- **CONNECTED:** the robot appeared, but the message lists missing sensor readings.
- **Not verified yet:** the wait ended. Installation may still be running, or a step may have failed.

The laptop waits for up to 20 minutes. Closing its window or pressing Ctrl+C stops that wait. It does not stop an installer already running on the robot.

Choose option **3** to read progress over SSH, using the robot's current IP. Or run these commands on the robot itself:

```bash
cat /var/lib/swarmlink-setup/status.json
sudo journalctl -u swarmlink-setup -n 40 --no-pager
```

The status shows `queued`, `wifi`, `packages`, `services`, `complete`, or `failed`. **Complete** means installation finished. The dashboard check confirms whether real sensor data arrived.

## Fix common problems

| Problem | Next step |
| --- | --- |
| Ethernet cable connected, but SSH cannot reach the robot | Check both Ethernet IPv4 addresses, the subnet mask, and the SSH service. The launcher does not configure this first connection. |
| Login or sudo fails | Use this robot's own account details. They may differ from Robot 01. |
| Wi-Fi fails | Check the exact hotspot name, password, and signal. The installer tries to restore the old network settings after a failed connection. |
| Package installation fails | Check internet access on the robot and read the setup log. Retry with the same robot ID. |
| The robot uses custom static Wi-Fi settings | Connect it locally first. Leave the hotspot field blank in the launcher. |
| Robot ID already used | Pick an unused ID for a new robot. For a retry, confirm that it is the same physical robot. |
| SSH disconnects during setup | Find the new address with `ip -br addr` on the robot, then use option 3. The background job may still be running. |
| Robot connected, sensors missing | Check OpenCR/LiDAR power, USB cables, the LiDAR model, and sensor logs. |
| Installation complete, no dashboard data | Check that the dashboard is running, devices can reach each other, TCP 8765 and UDP 8766 are allowed, and the robot key/run name match. |

Read the sensor and agent logs on the robot:

```bash
systemctl --user status swarmlink-bringup swarmlink-agent --no-pager
journalctl --user -u swarmlink-agent -u swarmlink-bringup -n 40 --no-pager
```

If SSH reports a changed host key, verify the robot before changing the saved host entry. The launcher keeps SSH's host checks enabled.

You can retry an SSH setup for the same robot. You can also rebuild an unfinished USB package after confirming it is for that same robot. The installer backs up existing SwarmLink files and changed Netplan files in its root-owned setup folder.

## Files behind the setup

These paths start inside `data-engineering/`.

| File | Job |
| --- | --- |
| `SETUP NEW ROBOT.cmd` | Opens the Windows setup window. |
| `scripts/setup-new-robot.py` | Starts the Python wizard. |
| `swarmlink_data/provisioning.py` | Asks questions, chooses an ID, creates the package, transfers it, and checks data. |
| `scripts/package-robot.py` | Packages the robot files and only that robot's key. |
| `deployment/robot/SETUP-ROBOT.sh` | Starts installation on the robot with sudo. |
| `deployment/robot/setup_robot.py` | Sets up Wi-Fi, packages, and startup services. |

OpenSSH handles the login passwords; the wizard does not save them. Generated packages, keys, and setup records stay in `.runtime/`, which Git ignores. The hotspot password stays in the robot's protected network profile so it can reconnect.

## What has been tested

Automated checks cover the launcher, package contents, input checks, duplicate IDs, Wi-Fi rollback, background startup, and live-data checks. **The full installer still needs a test on a second physical robot.** Ethernet-to-hotspot setup and a real reboot are part of that test. See [Document 06](06-validation.md).

Package and Wi-Fi setup follow the [ROS 2 Jazzy instructions](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html), [ROBOTIS guide](https://docs.robotis.com/docs/systems/turtlebot3/quick_start_guide/sbc_setup/), [NetworkManager keyfile format](https://networkmanager.dev/docs/api/latest/nm-settings-keyfile.html), and [Netplan examples](https://netplan.readthedocs.io/en/stable/examples/).
