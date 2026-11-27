# Document 02 - Current live robot deployment

Robot 01 is a TurtleBot3 Burger running ROS 2 Jazzy, domain 30, with an LDS-01. It connects to Selim's iPhone hotspot. Its recorded address was `172.20.10.2` and the PC was `172.20.10.5`; automatic discovery avoids depending on those leases.

## Start the collector on this PC

Double-click **Start Dashboard.exe** directly inside `data-engineering/`. It starts the collector in the background and opens http://127.0.0.1:8765 in the default browser. If the live collector is already running, it opens that instance instead of starting a duplicate. It uses the existing Python installation (preferring `.venv`), project dependencies, and provisioned keys; it is a launcher, not a bundled Python installer. Keep the executable in this folder.

The red **Shut down dashboard** button in the page's top bar stops the PC collector cleanly and leaves a stopped message in open dashboard tabs. The robot and its sensor services keep running and buffer events until the collector is started again, subject to the configured outbox capacity. Shutdown control is available only through a localhost address on the collector PC; LAN clients can view telemetry but cannot stop the collector.

Each collector start opens a fresh dashboard session. Previous readings, trails, counts, experiment results, and the dashboard's export view are reset. Refreshing the browser or opening the launcher while the collector is running keeps the same session. The banner shows the session start time and the **Session events** card counts newly accepted events since that start.

Raw records remain in the local archive for offline export. Previously unsent messages queued on the robot may arrive in the new session; old readings are marked stale. Retries of already saved events do not restore previous telemetry. See [Document 05](05-operations.md) for archive exports.

Startup errors appear in a Windows message box; collector output goes to `.runtime/dashboard.log`. Rebuild the small Windows executable from its included C# source with `./scripts/build-launcher.ps1`. A console alternative, from `data-engineering/`, is:

```powershell
./scripts/start-live.ps1
```

Open http://127.0.0.1:8765. The collector uses `.runtime/live` and the provisioned `.runtime/robot-keys.json`. It initially expects one robot and adds additional paired robot identities when their events arrive. The key file contains credentials for robot-01 through robot-04; only robot-01 is deployed so far. Do not overwrite that file or commit it.

The collector must be running for live display. The robot discovers it using authenticated UDP requests on port 8766, then sends events over TCP 8765. Keep both devices on the same reachable network. Broadcast did not produce a response on the tested hotspot, so discovery also checks local private IPv4 addresses, bounded to at most a /24. It does not use SSH passwords, scan other ports, or collect data from unpaired devices.

## Use another Wi-Fi network

Discovery does not depend on the iPhone hotspot name or its IP addresses. First connect the laptop and each provisioned robot to the new Wi-Fi network, saving that network's Wi-Fi settings on each robot. Start the collector and keep the robot agent running. This should work on other reachable local IPv4 networks; only the iPhone hotspot has been physically verified so far.

The network must allow devices to communicate, and the laptop firewall must permit incoming TCP 8765 and UDP 8766. Guest isolation, separate VLANs, or school network restrictions may prevent discovery or data transfer even when devices use the same Wi-Fi name. Internet access is not required for local telemetry once software is installed. Automatic discovery is bounded to 254 local unicast targets plus broadcast, so it does not guarantee coverage of a large campus network. A reachable fixed collector URL can be set with `SWARMLINK_COLLECTOR_URL=http://LAPTOP_IP:8765` in the robot's protected `.runtime/robot.env`, followed by restarting its agent service.

## Robot startup services

Files live under `/home/turtle/SwarmLink/data-engineering`. The user services are `swarmlink-bringup.service` and `swarmlink-agent.service`. The protected `.runtime/robot.env` supplies the robot ID, run ID, ROS domain, LiDAR model, battery encoding and its ingestion token. The sample run is `hotspot-live`, at 2 Hz with all 360 LiDAR rays retained.

Check them on the robot:

```bash
systemctl --user status swarmlink-bringup swarmlink-agent --no-pager
journalctl --user -u swarmlink-agent -n 30 --no-pager
loginctl show-user turtle -p Linger
```

Both services are enabled and running, and `Linger=yes` was verified so they can start without an interactive login. Bringup waits for USB devices and uses the normal TurtleBot startup. The agent queues events during a network/collector outage and rediscovers the collector on failure. Its service log confirmed discovery of the PC without a configured IP address, followed by live sensor ingestion. Startup at an actual power cycle must still be checked in the lab.

To stop data collection and the sensor bringup:

```bash
systemctl --user stop swarmlink-agent swarmlink-bringup
```

To disable future automatic startup as well:

```bash
systemctl --user disable swarmlink-agent swarmlink-bringup
```

## Enroll another robot once

The easiest route is **[SETUP NEW ROBOT.cmd](../data-engineering/SETUP%20NEW%20ROBOT.cmd)**. Follow [Document 07](07-new-robot-setup.md) to prepare another robot with a few inputs, over SSH or using a USB package. The commands below are the manual alternative.

After this setup, another robot on the same reachable hotspot appears automatically when its agent sends valid data. Simply joining Wi-Fi does not install or configure the agent. Use a unique robot ID and key, and the same `run_id` for robots you want to inspect together in one experiment.

First ensure its sensor topics are isolated from other robots by namespace or ROS domain. Match the agent's settings to its own drivers. Check its actual LiDAR model and battery encoding; do not assume they match robot-01.

Example bundle creation for a second Burger with LDS-01 and an intentionally separate local sensor domain:

```powershell
python scripts/package-robot.py --robot-id robot-02 --ros-domain 31 --battery-scale percent --lidar-model LDS-01
```

The generated `.runtime/robot-02-package.tar` contains a credential: transfer it by SSH/SCP, extract it under `~/SwarmLink/data-engineering`, and keep `.runtime/robot.env` readable only by its owner. This is a one-time provision for an authorized robot, not automatic enrollment of unknown network devices.

On that robot, after validating its bringup:

```bash
systemctl --user enable "$HOME/SwarmLink/data-engineering/deployment/robot/swarmlink-bringup.service" "$HOME/SwarmLink/data-engineering/deployment/robot/swarmlink-agent.service"
sudo loginctl enable-linger "$USER"
systemctl --user start swarmlink-bringup swarmlink-agent
```

Each subsequent arrival on the hotspot is automatic while the collector runs. If the team's swarm protocol needs a shared ROS domain, use proper per-robot topic namespaces instead of this separate-domain example.

## Rotate the displayed arrow

Select a robot and click **Rotate 180°** below Fleet position to switch its arrow between the received heading and a half-turn offset. The green pressed state means the offset is enabled. Robot 01 starts with the 180° offset enabled when the page loads; other robots start at zero. Changes last until the page is refreshed.

The button affects the displayed arrow only. Position, trajectory, LiDAR, stored/exported values, and the physical robot are unchanged. The map uses local X/Y coordinates; compass alignment controls have been removed. Independent robot origins are still kept separate.

## Measurement limits

The clock-offset indicator reflects a measured difference between the PC and robot clocks. It does not mean the live connection has failed. Synchronize the clocks before interpreting cross-device timing; negative apparent event ages are omitted rather than displayed as zero. Protocol performance remains blank until the team's protocol instrumentation emits real message/fault events.
