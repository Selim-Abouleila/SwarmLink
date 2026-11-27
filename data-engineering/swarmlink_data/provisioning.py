"""Guided robot enrollment using the installed OpenSSH client and standard Python."""
from __future__ import annotations

import argparse
import csv
import getpass
import io
import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from uuid import uuid4

from .desktop import URL, launch_dashboard
from .lease import DirectoryLease

ROOT = Path(__file__).resolve().parents[1]
USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:%_-]{0,253}")


def ask(label, default=None, choices=None):
    while True:
        value = input(f"{label}" + (f" [{default}]" if default is not None else "") + ": ").strip()
        value = value or default
        if value and (choices is None or value in choices):
            return value
        print("Please enter " + (" / ".join(choices) if choices else "a value") + ".")


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        result = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
        sid = next(csv.reader(io.StringIO(result.strip())))[1]
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
                       check=True, stdout=subprocess.DEVNULL)
    else:
        path.chmod(0o700)


def write_json(path, value):
    # Atomic replacement keeps reservations and credentials intact after interruption.
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def robot_keys(root):
    path = root / ".runtime/robot-keys.json"
    if not path.exists():
        write_json(path, {f"robot-{n:02d}": secrets.token_urlsafe(32) for n in range(1, 5)})
    keys = json.loads(path.read_text(encoding="utf-8"))
    if not keys or any(not re.fullmatch(r"robot-\d{2,3}", key) or
                       not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", token)
                       for key, token in keys.items()):
        raise ValueError("The robot key file is invalid. Existing keys were not changed.")
    return keys


def known_robots(root):
    database = root / ".runtime/live/fleet.sqlite"
    if not database.exists():
        return set()
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return {row[0] for row in db.execute("SELECT DISTINCT robot_id FROM events WHERE source='ros2'")}


def last_agent_session(root, robot_id):
    database = root / ".runtime/live/fleet.sqlite"
    if not database.exists():
        return None
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT session_id FROM events WHERE robot_id=? AND source='ros2' ORDER BY received_at DESC,rowid DESC LIMIT 1", (robot_id,)).fetchone()
        return row[0] if row else None


def ssh_options(root):
    return ["-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
            "-o", "StrictHostKeyChecking=ask", "-o", f'UserKnownHostsFile="{(root / ".runtime/setup/known_hosts").as_posix()}"']


def ssh_target(username, host, scp=False):
    if not USERNAME.fullmatch(username) or not HOST.fullmatch(host):
        raise ValueError("Use a Linux login name and a robot IP address or hostname, without shell commands.")
    return f"{username}@{('[' + host + ']') if scp and ':' in host else host}"


def remote_identity(root, username, host):
    # Authentication prompts remain in the real console; no password enters Python.
    probe = """import json,pathlib,platform,re
home=pathlib.Path.home()
inbox=home/'.local/state/swarmlink-setup-inbox'
if inbox.is_symlink(): raise RuntimeError('Setup inbox must not be a symlink')
inbox.mkdir(parents=True,exist_ok=True,mode=0o700)
inbox.chmod(0o700)
env=home/'SwarmLink/data-engineering/.runtime/robot.env'
match=re.search(r'^SWARMLINK_ROBOT_ID=(.*)$',env.read_text(),re.M) if env.exists() else None
print(json.dumps({'machine_id':pathlib.Path('/etc/machine-id').read_text().strip(),'os':platform.freedesktop_os_release(),'home':str(home),'robot_id':match[1].strip("'\\\"") if match else None}))
"""
    result = subprocess.run(["ssh", *ssh_options(root), ssh_target(username, host),
                             "python3 -c " + shlex.quote(probe)], check=True,
                            stdout=subprocess.PIPE, text=True)
    identity = json.loads(result.stdout.strip().splitlines()[-1])
    if identity["os"].get("ID") != "ubuntu" or identity["os"].get("VERSION_ID") != "24.04":
        raise ValueError("This installer supports Ubuntu 24.04 / ROS 2 Jazzy robots. No robot changes were made.")
    if not re.fullmatch(r"[a-f0-9]{32}", identity["machine_id"]):
        raise ValueError("The robot did not provide a valid machine identity.")
    return identity


def check_assignment(robot_id, identity, registry, seen, *, same_usb_robot=False):
    if identity and identity.get("robot_id") not in (None, robot_id):
        raise ValueError(f"This robot is already configured as {identity['robot_id']}; choose that identity to repair it.")
    assigned = registry.get(robot_id)
    if assigned:
        matches = identity and (assigned.get("machine_id") == identity.get("machine_id") or
                               assigned.get("machine_id") is None and identity.get("robot_id") == robot_id)
        usb_retry = same_usb_robot and identity is None and assigned.get("machine_id") is None and assigned.get("status") != "verified"
        if not matches and not usb_retry:
            raise ValueError(f"{robot_id} is already reserved. Use a different ID; never copy one robot's identity to another.")
    elif robot_id in seen and not (identity and identity.get("robot_id") == robot_id):
        raise ValueError(f"{robot_id} already sent physical telemetry. Choose an unused ID for the new robot.")
    if identity:
        other = next((key for key, value in registry.items()
                      if value.get("machine_id") == identity["machine_id"] and key != robot_id), None)
        if other:
            raise ValueError(f"This robot is already registered as {other}.")


def make_package(root, config):
    job = config["job_id"]
    folder = root / ".runtime/setup" / f"{config['robot_id']}-{job}"
    private_directory(folder)
    subprocess.run([sys.executable, str(root / "scripts/package-robot.py"),
                    "--robot-id", config["robot_id"], "--run-id", config["run_id"],
                    "--ros-domain", str(config["ros_domain"]), "--battery-scale", config["battery_scale"],
                    "--lidar-model", config["lidar_model"], "--model", config["model"]], check=True)
    shutil.copyfile(root / ".runtime" / f"{config['robot_id']}-package.tar", folder / "payload.tar")
    for name in ("SETUP-ROBOT.sh", "setup_robot.py"):
        shutil.copyfile(root / "deployment/robot" / name, folder / name)
    write_json(folder / "setup-config.json", config)
    return folder


def deploy_ssh(root, folder, username, host):
    target = ssh_target(username, host)
    # SCP uses the same strict host checking as the initial identity check.
    subprocess.run(["scp", *ssh_options(root), "-r", str(folder),
                    ssh_target(username, host, scp=True) + ":.local/state/swarmlink-setup-inbox/"], check=True)
    command = f"bash \"$HOME/.local/state/swarmlink-setup-inbox/{folder.name}/SETUP-ROBOT.sh\""
    result = subprocess.run(["ssh", *ssh_options(root), "-tt", target, command])
    if result.returncode:
        raise RuntimeError("SSH setup did not confirm launch. The robot may have changed Wi-Fi. Use option 3 to check its setup status before retrying.")


def wait_for_robot(robot_id, run_id, timeout=1200, previous_session=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    endpoint = URL + "/api/snapshot?" + urllib.parse.urlencode({"run_id": run_id})
    deadline = time.monotonic() + timeout
    connected_at = None
    last_report = 0
    while time.monotonic() < deadline:
        try:
            with opener.open(endpoint, timeout=3) as response:
                snapshot = json.load(response)
            robot = next((r for r in snapshot["robots"] if r["robot_id"] == robot_id), None)
            if (snapshot.get("run_id") == run_id and robot and robot.get("source") == "ros2"
                    and robot.get("status") == "online" and robot.get("receipt_age_s", 99) < 6
                    and (previous_session is None or robot.get("session_id") != previous_session)):
                connected_at = connected_at or time.monotonic()
                payload = robot["payload"]
                missing = [name for name, field in (("odometry", "pose"), ("LiDAR", "scan"), ("battery", "battery_pct"))
                           if payload.get(field) is None]
                if not missing:
                    print(f"\nSUCCESS: {robot_id} is sending odometry, LiDAR and battery readings to the dashboard.")
                    return True
                if time.monotonic() - connected_at > 45:
                    print(f"\nCONNECTED: {robot_id} appeared, but these sensors need checking: {', '.join(missing)}.")
                    return False
        except (OSError, ValueError, KeyError):
            pass
        if time.monotonic() - last_report > 30:
            print("Waiting for the robot... Package installation can take 10-20 minutes. Ctrl+C stops this wait only.")
            last_report = time.monotonic()
        time.sleep(3)
    print("\nNot verified yet. Run this launcher again and choose 'Check a setup'. See Document 07 for troubleshooting.")
    return False


def wizard(root):
    print("\nSWARMLINK - SETUP NEW ROBOT\n")
    print("Ubuntu 24.04 TurtleBot3. Keep the robot powered during installation.")
    print("1. Set up over SSH (Ethernet or an existing network)")
    print("2. Prepare USB setup (robot is not on a network yet)")
    print("3. Check a setup already started")
    mode = ask("Choose", "1", ("1", "2", "3"))
    username = ask("Robot Linux username", "turtle")
    if not USERNAME.fullmatch(username):
        raise ValueError("Invalid Linux username.")
    host = None
    if mode in ("1", "3"):
        if not shutil.which("ssh") or not shutil.which("scp"):
            raise RuntimeError("Enable Windows OpenSSH Client in Optional features, or use the USB option.")
        host = ask("Robot's current IP address or hostname")
        ssh_target(username, host)
        print("SSH may ask you to verify the robot's host fingerprint and enter its login password.")
    if mode == "3":
        subprocess.run(["ssh", *ssh_options(root), "-tt", ssh_target(username, host),
                        "cat /var/lib/swarmlink-setup/status.json; sudo journalctl -u swarmlink-setup -n 35 --no-pager"], check=True)
        return
    identity = remote_identity(root, username, host) if mode == "1" else None
    keys = robot_keys(root)
    registry_path = root / ".runtime/setup/robots.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    seen = known_robots(root)
    available = sorted(set(keys) - seen - set(registry))
    same = (identity.get("robot_id") if identity and identity.get("robot_id") in keys else None) or next(
        (key for key, item in registry.items() if identity and item.get("machine_id") == identity["machine_id"]), None)
    usb_pending = sorted(key for key, item in registry.items() if mode == "2" and item.get("machine_id") is None and item.get("status") != "verified")
    if not available and not same and not usb_pending:
        raise ValueError("All provisioned robot IDs are used. Add more collector keys before enrolling another robot.")
    print("Unused robot IDs: " + ", ".join(available))
    if usb_pending:
        print("USB setups that can be retried for the SAME robot: " + ", ".join(usb_pending))
    robot_id = ask("Robot ID", same or (available or usb_pending)[0], tuple(available + usb_pending + ([same] if same else [])))
    usb_retry = robot_id in usb_pending
    if robot_id == same or usb_retry:
        if input(f"This repairs/resumes {robot_id}. Type {robot_id} to confirm it is the SAME physical robot: ").strip() != robot_id:
            raise ValueError("Robot identity confirmation did not match.")
    check_assignment(robot_id, identity, registry, seen, same_usb_robot=usb_retry)
    model = ask("TurtleBot3 model", "burger", ("burger", "waffle", "waffle_pi"))
    lidar = ask("LiDAR model printed on the sensor", "LDS-01", ("LDS-01", "LDS-02", "LDS-03"))
    battery = ask("Battery publisher scale (current Burger uses percent)", "percent", ("percent", "fraction"))
    run_id = ask("Shared experiment name", "hotspot-live")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", run_id):
        raise ValueError("Invalid experiment name.")
    domain = 29 + int(robot_id.split("-")[1])
    if not 0 <= domain <= 232:
        raise ValueError("This robot ID needs a manually chosen ROS domain; see Document 07.")
    ssid = input("Exact hotspot Wi-Fi name (Enter to keep current Wi-Fi): ")
    password = getpass.getpass("Hotspot Wi-Fi password (hidden): ") if ssid else ""
    if ssid and (not 1 <= len(ssid.encode()) <= 32 or any(c in ssid for c in "\r\n\0")):
        raise ValueError("Wi-Fi names must contain 1-32 UTF-8 bytes and no line breaks.")
    if ssid and not (8 <= len(password.encode()) <= 63 or re.fullmatch(r"[a-fA-F0-9]{64}", password)):
        raise ValueError("Use a WPA personal hotspot password: 8-63 bytes, or a 64-digit hex key.")
    if any(c in password for c in "\r\n\0"):
        raise ValueError("Wi-Fi passwords cannot contain line breaks.")
    config = {"job_id": uuid4().hex, "username": username, "robot_id": robot_id, "run_id": run_id,
              "ros_domain": domain, "model": model, "lidar_model": lidar, "battery_scale": battery,
              "machine_id": identity["machine_id"] if identity else None, "ssid": ssid, "wifi_password": password}
    print(f"\nReady: {robot_id}, {model}, {lidar}, ROS domain {domain}, experiment {run_id}.")
    print("Wi-Fi: " + (ssid or "keep existing connection") + ". Installs required ROS packages and enables sensor/agent startup.")
    if ask("Start this setup?", "yes", ("yes", "no")) != "yes":
        return
    folder = make_package(root, config)
    # Reserve before transferring: a failed or interrupted setup must not reuse this ID.
    registry[robot_id] = {"machine_id": config["machine_id"], "host": host, "job_id": config["job_id"],
                          "package": str(folder), "run_id": run_id, "status": "prepared"}
    write_json(registry_path, registry)
    launch_dashboard(root, open_browser=True)
    previous_session = last_agent_session(root, robot_id)
    if mode == "1":
        print("\nTransferring the private package, then starting installation. SSH and sudo may ask for the robot password.")
        deploy_ssh(root, folder, username, host)
        (folder / "setup-config.json").unlink()  # Wi-Fi password is no longer needed on the PC.
    else:
        archive = folder.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in folder.iterdir():
                bundle.write(path, folder.name + "/" + path.name)
        print(f"\nCopy and extract this private ZIP onto a USB drive:\n{archive}")
        print("On the robot, open its extracted folder in a terminal and run: bash SETUP-ROBOT.sh")
        print("Enter the robot's sudo password when asked. The package contains Wi-Fi details and this robot's key; keep it private.")
        input("Press Enter after starting that script on the robot to check the dashboard... ")
    verified = wait_for_robot(robot_id, run_id, previous_session=previous_session)
    registry[robot_id]["status"] = "verified" if verified else "needs_check"
    write_json(registry_path, registry)


def main():
    parser = argparse.ArgumentParser(description="Guided SSH or USB enrollment for an Ubuntu 24.04 TurtleBot3")
    parser.parse_args()
    try:
        private_directory(ROOT / ".runtime/setup")
        lease = DirectoryLease(ROOT / ".runtime/setup")
        try:
            wizard(ROOT)
        finally:
            lease.close()
        return 0
    except KeyboardInterrupt:
        print("\nSetup window cancelled. A robot installer already launched continues on the robot. Use option 3 to check it.")
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"\nSetup stopped: {exc}\nSee documentation/07-new-robot-setup.md for the next step.")
        return 1
