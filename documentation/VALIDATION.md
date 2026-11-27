# Validation record

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

From `data-engineering/`, run `python -m pytest -q` to obtain the current result. Check the dashboard syntax with `node --check swarmlink_data/static/app.mjs`. The API checks also verify the JavaScript MIME type for the dashboard module. The Rotate 180° control is checked in the live browser.

The current Python suite has 64 passing cases, including authenticated UDP discovery, replay/address tampering rejection, hotspot subnet derivation, explicit battery scaling, clock-offset handling, shutdown notification and data preservation, local control authorization, and launcher reuse/startup errors. Two deprecation warnings originate from the installed Uvicorn/WebSockets integration.

## Physical robot verification, 27 November 2026

The Windows dashboard lifecycle was also exercised against the real collector: the red button displayed its stopped state and closed the HTTP listener; `Start Dashboard.exe` restarted the collector and opened the default browser. A second launch reused the same listening process. Live robot ingestion resumed with preserved prior records, zero rejected events, zero sequence gaps, and an empty reported robot outbox. The running collector was left available afterward.

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

## Remaining physical acceptance

Run the checklist in `ROBOT_SETUP.md` against all four robots. Verify their topic isolation, ROS versions/QoS, clocks, hotspot peer reachability, and the team's protocol instrumentation. A complete power-cycle test remains a team check; service enablement alone is not proof of boot behavior. Neither the fixture nor a passing API test demonstrates physical sensor accuracy, protocol correctness, or a production latency/throughput target.
