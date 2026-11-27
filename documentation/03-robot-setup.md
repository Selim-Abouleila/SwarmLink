# Document 03 - Connect four TurtleBots to the data plane

The current lab context is TurtleBot3 / ROS 2 Jazzy, a Windows collector PC, and Selim's iPhone hotspot. Robot 01 uses ROS domain 30 and an LDS-01, verified from its serial packets and live scans. See [02-live-robot-guide.md](02-live-robot-guide.md) for the working deployment. Hotspot DHCP addresses may change; automatic discovery handles that for paired agents.

## 1. Bring the network up

Enable Personal Hotspot on the iPhone and connect **both the PC and all four robots**. Each robot should have its hotspot profile saved with autoconnect. Keep Wi-Fi credentials out of this repository. If the robot already auto-connects, do not replace its working network configuration.

On the PC, use `ipconfig` to identify the hotspot adapter's IPv4 address. The agent's `--collector` is the **PC address**, not the robot's address. Do not hard-code the earlier `172.20.10.x` addresses without checking them again.

Set up the host environment using the [data engineering README](../data-engineering/README.md), and run laptop commands from `data-engineering/`. Generate four robot keys only on a fresh installation, then start live mode. On the existing PC the keys are already provisioned; use `scripts/start-live.ps1`:

```powershell
python -m swarmlink_data keys --robots 4 --output .runtime/robot-keys.json
python -m swarmlink_data serve --host 0.0.0.0 --port 8765 --keys .runtime/robot-keys.json --robots 1
```

On a robot, replace the example address and check the HTTP route:

```bash
curl --max-time 5 http://PC_HOTSPOT_IP:8765/api/health
```

Expected response includes `"status":"ok"` and `"mode":"live"`. If it fails, check the PC's listening address, hotspot client reachability, and a narrowly scoped Windows firewall allowance for this Python server/TCP 8765. Do not disable the firewall. If the hotspot isolates clients, use a lab router or a suitable private VPN; internet access alone does not prove robot-to-PC reachability.

The iPhone carries the local connection; no cloud account is required. The collector must remain running and reachable for live updates. Outages are buffered on each robot.

## 2. Give every robot an identity

| Physical unit | Agent identity | Key from `.runtime/robot-keys.json` |
| --- | --- | --- |
| TurtleBot 1 | `robot-01` | Value for `robot-01` |
| TurtleBot 2 | `robot-02` | Value for `robot-02` |
| TurtleBot 3 | `robot-03` | Value for `robot-03` |
| TurtleBot 4 | `robot-04` | Value for `robot-04` |

Use `scripts/package-robot.py` to create a bundle for a specific robot, with only its assigned key. Copy that private bundle over SSH/SCP and extract it under `~/SwarmLink/data-engineering`. The runtime needs `agents/ros2_agent.py` and `swarmlink_data/{__init__,edge,discovery}.py`. Do not copy the host's entire `.runtime/`, `.venv/`, or shared key file to robots.

For multiple physical robots, separate their sensor topics using unique ROS namespaces or separate ROS domains and match each agent to its local drivers. Starting unnamespaced `/scan` and `/odom` publishers for multiple robots in the same ROS domain can mix their readings. The single-robot deployment uses domain 30; choose and validate the remaining robots' settings before enrollment.

The robot adapter uses the Python standard library plus its existing `rclpy`, `nav_msgs`, and `sensor_msgs`. It does not need FastAPI, pandas, PyArrow, or `rosidl_runtime_py` installed on the robot.

## 3. Verify robot topics and start collection

The robotics team should start the robot's existing sensor/odometry bringup. This collector does not do it automatically. In the robot terminal:

```bash
source /opt/ros/jazzy/setup.bash
source ~/turtlebot3_ws/install/setup.bash
export ROS_DOMAIN_ID=30  # match this robot's running nodes; do not assume 30 everywhere
```

When the ROS topic CLI is available, `ros2 topic list` should show `/odom`, `/scan`, and `/battery_state`. This robot initially lacked ROS CLI extensions; `ros-jazzy-ros2launch` and `ros-jazzy-rosidl-runtime-py` have now been installed. The adapter subscribes through `rclpy` and does not require the topic CLI. The dashboard's per-topic ages and missing values provide another check.

Read the assigned token into the shell without putting it in command history:

```bash
read -rsp 'This robot’s collector token: ' SWARMLINK_ROBOT_TOKEN
echo
export SWARMLINK_ROBOT_TOKEN
```

Run on robot 01:

```bash
python3 ~/SwarmLink/data-engineering/agents/ros2_agent.py \
  --robot-id robot-01 \
  --run-id lab-baseline-001 \
  --battery-scale percent \
  --hz 2 --max-rays 360
```

Omitting `--collector` enables authenticated discovery over UDP 8766. The agent uses broadcast plus a bounded search of directly attached private IPv4 addresses (at most a /24), because this iPhone hotspot blocks client broadcast. No token is sent in discovery packets; challenge responses are authenticated with the robot's provisioned key. Actual data then uses HTTP on TCP 8765. An explicit `--collector http://PC_IP:8765` is available as a fixed-address fallback.

Repeat on the other three robots with their own ID/token and **the same** `--run-id`. Use `--odom-topic`, `--scan-topic`, and `--battery-topic` if the team's namespaced topics differ. Keep each agent in the same ROS domain as its local drivers. The PC does not need ROS or cross-hotspot DDS discovery.

The `--battery-scale percent` setting above matches the observed TurtleBot publisher, which sends values such as `94.44`. Standard ROS publishers send a 0–1 fraction; use the default `--battery-scale fraction` for those. Select the encoding explicitly rather than guessing low battery values.

If the protocol adapter uses nondefault metadata, pass matching `--scenario` and `--protocol` to this agent. For example, both producers could use `--scenario transient-loss --protocol TEAM_PROTOCOL`. A new experimental configuration should get a new run ID.

## 4. What to expect

Within a few seconds, the dashboard should show four online agents. Select each one and verify battery (if published), LiDAR, linear speed, and local odometry. Odometry is displayed in the selected robot's local frame until the robotics team supplies a real common transform/map.

The agent emits up to 2 samples/s by default, not every original ROS message. It subscribes with sensor-data QoS and retains the latest messages. Topic age is measured with a monotonic clock; after three seconds without a callback, that topic's measurements are absent from new samples. A live heartbeat with an empty sensor payload means the collector process is running but sensors are unavailable.

The default outbox is `~/.local/state/swarmlink/outbox.sqlite`. Pending events survive an agent restart. HTTP retries retain event IDs; the collector suppresses duplicates. The 50,000-event capacity is about 6.9 hours at 2 Hz, before protocol events, and depends on disk availability. At capacity new samples are refused with an error rather than overwriting pending history. Monitor storage and restore connectivity.

The optional services under `deployment/robot/` start bringup and collection at boot. Their generated `.runtime/robot.env` has mode 0600 and contains only the corresponding robot's credential and configuration. Enable the user services and user lingering as described in `02-live-robot-guide.md`. Confirm interactive sensor collection first. The bringup wrapper waits for its USB devices; the agent retries discovery and retains queued events while the PC or hotspot is unavailable.

## 5. Physical acceptance check

1. Verify all four IDs, topics and clocks against the actual robot consoles.
2. Disconnect one robot from the hotspot for roughly 15 seconds; confirm the other three continue updating and its card becomes stale.
3. Reconnect it; confirm buffered events arrive without increasing unique counts on a retry, then fresh telemetry restores its online status.
4. Restart one agent; confirm its new session permits sequence zero while older queued events remain intact.
5. Export a run and compare rows, timestamps, units, and scan angles against the robot measurements.
6. Integrate protocol events and compare delivery/fault measurements against known controlled experiments. A Wi-Fi dropout alone does not constitute a verified Byzantine or crash-fault experiment.

These hardware checks remain to be performed by the team. The local demo and automated tests do not substitute for them.
