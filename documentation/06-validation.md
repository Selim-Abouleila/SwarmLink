# Document 06 - Validation record

Verified locally on Windows with Python 3.11.2, and with one physical TurtleBot on Ubuntu 24.04 / ROS 2 Jazzy. Four physical robots have not yet been tested together.

## Automated checks

- Contract rejection for invalid versions, identities, timestamps, ranges, numbers, and unknown fields.
- Event ID deduplication, changed-content rejection, session/sequence collisions, robot restart, late-arrival gap closure, and nonregressing latest state.
- Retention/recovery of complete raw receipts after an uncommitted materialization, torn-tail handling, complete-corruption failure, and exclusion of a second writer.
- Per-robot authentication, cross-robot batch rejection, simulation/live separation, bounded request sizes, four-robot snapshots, WebSocket delivery, and export endpoints.
- Persistent edge queue, idempotent retries, all-or-nothing acknowledgement validation, local rejection quarantine, and refusal to overwrite a full outbox.
- LiDAR angular sampling and invalid-return conversion.
- Recipient-based delivery metrics, retransmission handling, first detection, unknown metric nulls, and recovery measured from fault end.
- Four- and twenty-robot fixture correctness, pause/dropout behavior, Parquet scan/null/ID preservation, repeatable exports and replay deduplication.
- Fresh collector sessions clear all dashboard read models while preserving the offline archive and duplicate detection, including journal recovery before the reset boundary.
- Session-scoped protocol metrics, API/WebSocket state and exports, and a second paired robot appearing without a pre-created dashboard card.

From `data-engineering/`, run `python -m pytest -q` to obtain the current result. Check the dashboard syntax with `node --check swarmlink_data/static/app.mjs`. The API checks also verify the JavaScript MIME type for the dashboard module. The Rotate 180° control is checked in the live browser.

The current Python suite has 94 passing cases, including authenticated UDP discovery, replay/address tampering rejection, hotspot subnet derivation, explicit battery scaling, clock-offset handling, shutdown notification and archive preservation, fresh dashboard sessions, local control authorization, launcher reuse/startup errors, and the guided robot installer. Two deprecation warnings originate from the installed Uvicorn/WebSockets integration.

## Guided installer checks

The new setup tests build actual temporary packages and verify that only the chosen robot's key is included. They cover unsafe archive paths, malformed inputs, shell characters in hotspot names/passwords, duplicate robot IDs, same-machine retries, SSH quoting with spaces in the repository path, selected LiDAR packages, Wi-Fi failure rollback, and a detached systemd installer launch. A scripted wizard test selects an unused ID without changing existing keys. Dashboard verification distinguishes fresh sensor readings from stale or missing data.

The Windows `SETUP NEW ROBOT.cmd --help` entry point and Bash syntax were checked locally. The installer tests mock operating-system package, network, and service commands. **The complete installer has not been executed on a second physical robot**, so package installation, Wi-Fi switching, hardware startup, and reboot behavior still need that acceptance check. See [Document 07](07-new-robot-setup.md).

## Physical robot verification, 27 November 2026

The Windows dashboard lifecycle was exercised against the real collector: the red button displayed its stopped state and closed the HTTP listener; `Start Dashboard.exe` restarted the collector and opened the default browser. A second launch reused the same listening process.

After the session-reset change, a graceful stop and launcher restart changed the collection ID and reset the session event count from 520 to 0, with no previous history in the first snapshot. Robot 01 reconnected automatically at about 2 events/s, with zero rejected events, zero sequence gaps, and an empty reported outbox. A CSV export contained only the new session's events; older events were still present in the archive. The running collector was left available afterward.

- Matched the saved SSH host key at the robot's hotspot address, then authenticated with the owner's supplied credentials.
- Installed the missing ROS launch and message runtime packages; verified `ros2 launch --help` and all three message imports.
- Identified LDS-01 packet framing on `/dev/ttyUSB0`; OpenCR is on `/dev/ttyACM0`.
- Started the standard Burger bringup. A 12-second subscription check received 62 scans, 240 odometry messages and 241 battery messages.
- Observed 360-ray scans, stationary odometry, and approximately 12.2 V / 94.4% battery. The TurtleBot publishes battery on a 0–100 scale, so the agent uses `--battery-scale percent`.
- Verified real authenticated HTTP ingestion at 2 Hz, zero rejected events, one online robot, and the live LiDAR view in the browser.
- Measured a PC/robot clock offset; negative event ages are excluded and visibly flagged. No system clocks were changed.
- Verified discovery across the actual hotspot without a configured PC address, using local unicast probes when broadcasts did not receive a response.
- Validated the startup scripts and systemd units, enabled both robot services and user lingering, then verified active services and resumed real ingestion after replacing the manually started processes.

## Browser verification

Checked the running local dashboard in the Codex browser at desktop and 390px phone widths:

- Four robot cards and shared-frame synthetic trajectories update live.
- Selecting Robot 03 changes the LiDAR/detail panel.
- Pausing the synthetic feed changes all four robots to stale; resuming restores updates.
- The synthetic dropout action displays its duration and expected behavior; the fleet resumes afterward.
- The export menu and Parquet download complete.
- The phone layout fits without page-wide horizontal overflow; experiment tables scroll within their panel.
- No browser console errors or warnings appeared during these interactions.

For the session and logo update, the live browser showed the mint logo in the header and navigation, a session start time, the new **Session events** card, and resumed real sensor readings. No console errors or warnings were recorded. The served PNG matched `documentation/images/swarmlink-logo.png` byte for byte. The root README and documentation links were checked against their local targets.

## Remaining physical acceptance

Run the checklist in [Document 03](03-robot-setup.md) against all four robots. Verify their topic isolation, ROS versions/QoS, clocks, hotspot peer reachability, and the team's protocol instrumentation. A complete power-cycle test remains a team check; service enablement alone is not proof of boot behavior. Neither the fixture nor a passing API test demonstrates physical sensor accuracy, protocol correctness, or a production latency/throughput target.
