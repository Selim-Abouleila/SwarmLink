# Document 1 SwarmLink dashboard version 1

## What this version does

The goal is to collect useful data from our robots and show it in one place. This is the data engineering part of SwarmLink. It takes the sensor readings, checks them, saves them, and makes them available on a live dashboard.

The first version has been tested with one real TurtleBot3 Burger. The project is planned for at least four robots. Each robot will need its own ID and a small program installed before it can send data.

The normal dashboard uses real robot readings. There is also a separate demo mode for testing with simulated data.

## What appears on the dashboard

- Battery level and movement speed.
- An estimated position and direction from odometry. Odometry is the robot's estimate of how it has moved.
- A LiDAR scan showing distances to objects around the robot.
- Connection status and how recently data arrived.
- A list of recent messages and counts of accepted, repeated, or rejected messages.

The map shows the selected robot's own coordinates. It is not a complete map of the room. Different robots may start from different places, so their positions are kept separate unless they use a real shared map.

The **Rotate 180°** button flips the selected robot's arrow on the screen. Clicking again restores its original direction. It does not turn the robot or change the saved data. Robot 01 starts with this display flip enabled, and refreshing the page restores that default.

## How the data reaches the screen

The basic path is:

**Robot sensors → Python agent on the robot → Collector on the laptop → Saved data and live dashboard**

The robot uses ROS 2, which lets programs exchange sensor readings. The main script on the robot is `agents/ros2_agent.py`. It reads three channels, called topics:

- `/scan` gives LiDAR distances.
- `/odom` gives estimated position, direction, and speed.
- `/battery_state` gives battery information.

For our current robot, the script puts the latest readings into a message about twice per second. Each message includes the robot ID, a time, a sequence number, and the sensor values. Old or missing readings are left unavailable instead of being made up. The script reads data and does not send movement commands.

The laptop receives these messages through the Python program in `swarmlink_data/app.py`. It checks the robot's key and the data format. The dashboard then receives updates about once per second, without needing a manual page refresh.

## How the data is saved

If the connection drops, `swarmlink_data/edge.py` keeps outgoing messages in a small database on the robot. It tries to send them again when the laptop is reachable. This queue has a size limit.

Every message has an ID. If the same message arrives twice after a retry, the collector recognises it and avoids counting it twice.

On the laptop, `swarmlink_data/store.py` keeps a raw log of received messages and stores accepted data in a SQLite database. SQLite is a database saved in a local file. It makes it easier to find recent readings and past runs. The raw log uses JSONL, which stores one record per line.

The **Export** button can download a run as CSV or Parquet. CSV is easy to open in a spreadsheet. Parquet is useful for analysing larger amounts of data later.

## The main files

| File | What it does |
| --- | --- |
| `agents/ros2_agent.py` | Reads the robot's sensor topics and prepares messages. |
| `swarmlink_data/discovery.py` | Helps a prepared robot find the laptop collector on the network. |
| `swarmlink_data/app.py` | Receives data and serves the dashboard. |
| `swarmlink_data/store.py` | Checks incoming records and saves the results. |
| `swarmlink_data/static/app.mjs` | Updates the page, draws the charts, and handles the buttons. |
| `swarmlink_data/static/index.html` and `styles.css` | Define the page layout, colours, and appearance. |
| `swarmlink_data/desktop.py` | Starts the collector or opens it if it is already running. |

`Start Dashboard.exe` is a small Windows launcher. Its source is in `scripts/DashboardLauncher.cs`. It runs the Python startup script and opens the browser. Python and the project dependencies still need to be installed on the laptop.

## Starting and stopping

1. Connect the laptop and the prepared robot to the same reachable network.
2. Make sure the robot is powered on and its sensor and agent services are running.
3. Double-click **Start Dashboard.exe** inside `data-engineering`.
4. Open the robot card to see its readings. Additional prepared robots appear when their messages arrive.
5. Use the red **Shut down dashboard** button to stop the laptop collector.

The shutdown button keeps saved data and leaves the robot running. Starting the dashboard again lets the robot send its queued messages. Opening the launcher twice reuses the running collector.

## Using another Wi-Fi network

The system is not tied to a particular hotspot name. Each robot must first be connected to the new Wi-Fi network through its network settings. Once connected, the agent can look for the laptop automatically. The laptop's IP address does not have to stay fixed.

The network and laptop firewall must allow the devices to talk to each other. Some guest or school networks block this. Discovery uses UDP port 8766 and data transfer uses TCP port 8765. Discovery checks a limited local address range, so large networks may need the laptop address to be configured manually. Local data transfer does not need internet access after installation.

So far, the real connection has been tested on an iPhone hotspot. Other networks still need a practical check.

## What still needs work

The next main step is to test four real robots together. Their sensor topics need to stay separate so that one robot's readings are not mixed with another's.

The robot and laptop clocks also need to agree before we use timing values to judge delays. The dashboard shows a clock warning when they do not match. A stale robot card means recent data is missing; it does not prove the robot has failed.

The experiment section is ready for later work on communication and faults. Those results stay blank until the team's robot software sends the required events. Sensor readings alone cannot tell us whether the swarm algorithm is correct.

This first version gives us a working way to collect, save, view, and export real robot data. The setup details are in [docs/LIVE_ROBOT.md](docs/LIVE_ROBOT.md), and the technical checks are in [docs/VALIDATION.md](docs/VALIDATION.md).
