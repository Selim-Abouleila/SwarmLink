#!/usr/bin/env python3
"""Ubuntu 24.04 installer, launched with sudo and detached before Wi-Fi changes.

Only the generated bundle is installed. No motor commands or firmware flashing.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path, PurePosixPath
from uuid import uuid4

BASE = Path("/var/lib/swarmlink-setup")
RUNTIME_FILES = {
    "agents/ros2_agent.py", "swarmlink_data/__init__.py", "swarmlink_data/edge.py", "swarmlink_data/discovery.py",
    "deployment/robot/prepare-runtime.sh", "deployment/robot/start-agent.sh", "deployment/robot/start-bringup.sh",
    "deployment/robot/swarmlink-agent.service", "deployment/robot/swarmlink-bringup.service", ".runtime/robot.env",
}
LIDAR_PACKAGES = {"LDS-01": "hls-lfcd-lds-driver", "LDS-02": "ld08-driver", "LDS-03": "coin-d4-driver"}


def validate(config):
    patterns = {"job_id": r"[a-f0-9]{32}", "username": r"[a-z_][a-z0-9_-]{0,31}",
                "robot_id": r"robot-\d{2,3}", "run_id": r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}"}
    for name, pattern in patterns.items():
        if not isinstance(config.get(name), str) or not re.fullmatch(pattern, config[name]):
            raise ValueError(f"Invalid {name} in the setup package.")
    if type(config.get("ros_domain")) is not int or not 0 <= config["ros_domain"] <= 232:
        raise ValueError("Invalid ROS domain.")
    if config.get("model") not in ("burger", "waffle", "waffle_pi") or config.get("lidar_model") not in LIDAR_PACKAGES:
        raise ValueError("Unsupported TurtleBot3 or LiDAR model.")
    if config.get("battery_scale") not in ("percent", "fraction"):
        raise ValueError("Invalid battery encoding.")
    ssid, password = config.get("ssid"), config.get("wifi_password")
    if not isinstance(ssid, str) or not isinstance(password, str) or any(c in ssid + password for c in "\r\n\0"):
        raise ValueError("Invalid Wi-Fi details.")
    if ssid and (not 1 <= len(ssid.encode()) <= 32 or not (8 <= len(password.encode()) <= 63 or re.fullmatch(r"[a-fA-F0-9]{64}", password))):
        raise ValueError("Invalid WPA personal hotspot name/password length.")
    if config.get("machine_id") is not None and not re.fullmatch(r"[a-f0-9]{32}", config["machine_id"]):
        raise ValueError("Invalid machine identity.")
    return config


def run(args, *, capture=False, check=True, timeout=1800, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE if capture else None, text=True, check=False,
                            timeout=timeout, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LANG": "C.UTF-8"}, **kwargs)
    if check and result.returncode:
        # Do not include argument values or captured output in exceptions/status files.
        raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode}). See the setup service log.")
    return result


def save(path, text, mode=0o600):
    if path.is_symlink():
        raise ValueError("Refusing to replace a symlink: " + str(path))
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    # The background job uses umask 0077; status is intentionally readable
    # without sudo, while credentials keep the default private mode.
    temporary.chmod(mode)
    temporary.replace(path)


def status(config, phase, message):
    save(BASE / "status.json", json.dumps({"job_id": config["job_id"], "robot_id": config["robot_id"],
         "phase": phase, "message": message, "updated_at": time.time()}, indent=2) + "\n", 0o644)
    print(f"{phase}: {message}", flush=True)


def payload_files(path):
    data = {}
    with tarfile.open(path, "r") as archive:
        for entry in archive.getmembers():
            name = str(PurePosixPath(entry.name))
            if not entry.isfile() or entry.name != name or ".." in PurePosixPath(name).parts or name.startswith("/"):
                raise ValueError("Unsafe file in robot payload.")
            if entry.size > 2_000_000 or name in data:
                raise ValueError("Oversized or duplicate file in robot payload.")
            if name not in RUNTIME_FILES:
                if name in ("deployment/robot/setup_robot.py", "deployment/robot/SETUP-ROBOT.sh"):
                    continue
                raise ValueError("Unexpected file in robot payload: " + name)
            data[name] = archive.extractfile(entry).read()
    if set(data) != RUNTIME_FILES:
        raise ValueError("Robot payload is incomplete; recreate the setup package.")
    return data


def environment_values(data):
    result = {}
    for line in data.decode("utf-8").splitlines():
        name, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ValueError("Invalid robot environment file.")
        words = shlex.split(value)
        if len(words) != 1:
            raise ValueError("Invalid robot environment value.")
        result[name] = words[0]
    return result


def check_payload(config, files):
    values = environment_values(files[".runtime/robot.env"])
    expected = {"SWARMLINK_ROBOT_ID": config["robot_id"], "SWARMLINK_RUN_ID": config["run_id"],
                "ROS_DOMAIN_ID": str(config["ros_domain"]), "TURTLEBOT3_MODEL": config["model"],
                "LDS_MODEL": config["lidar_model"], "SWARMLINK_BATTERY_SCALE": config["battery_scale"]}
    if any(values.get(k) != v for k, v in expected.items()):
        raise ValueError("The robot payload does not match the setup choices.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", values.get("SWARMLINK_ROBOT_TOKEN", "")):
        raise ValueError("Invalid robot credential in payload.")


def target_user(config):
    import pwd
    if os.geteuid() != 0:
        raise RuntimeError("Run SETUP-ROBOT.sh; it asks sudo for the required installation permissions.")
    release = platform.freedesktop_os_release()
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04" or platform.machine() not in ("aarch64", "x86_64"):
        raise RuntimeError("This installer supports 64-bit Ubuntu 24.04 / ROS 2 Jazzy only.")
    user = pwd.getpwnam(config["username"])
    if user.pw_uid == 0 or not Path(user.pw_dir).is_dir():
        raise ValueError("Choose the robot's ordinary login user, not root.")
    actual = Path("/etc/machine-id").read_text().strip()
    if config["machine_id"] and config["machine_id"] != actual:
        raise ValueError("This setup package belongs to a different robot.")
    existing = Path(user.pw_dir) / "SwarmLink/data-engineering/.runtime/robot.env"
    if existing.exists():
        old_id = environment_values(existing.read_bytes()).get("SWARMLINK_ROBOT_ID")
        if old_id != config["robot_id"]:
            raise ValueError(f"This robot is already configured as {old_id}; its identity was not changed.")
    return user


def keyfile_escape(value):
    return value.replace("\\", "\\\\").replace(" ", "\\s")


def nm_profile(config, interface, profile_id):
    return (f"[connection]\nid=SwarmLink-{config['robot_id']}\nuuid={profile_id}\ntype=wifi\n"
            f"interface-name={interface}\nautoconnect=true\nautoconnect-priority=100\n\n"
            f"[wifi]\nmode=infrastructure\nssid={keyfile_escape(config['ssid'])}\n\n"
            f"[wifi-security]\nkey-mgmt=wpa-psk\npsk={keyfile_escape(config['wifi_password'])}\n\n"
            "[ipv4]\nmethod=auto\nroute-metric=100\n\n[ipv6]\nmethod=auto\n")


def netplan_profile(config, interface):
    # JSON is valid YAML and safely quotes apostrophes, spaces and metacharacters.
    return json.dumps({"network": {"version": 2, "wifis": {interface: {
        "renderer": "networkd", "dhcp4": True, "optional": True,
        "dhcp4-overrides": {"route-metric": 100},
        "access-points": {config["ssid"]: {"password": config["wifi_password"]}}}}}}, indent=2) + "\n"


def netplan_without_old_wifi(text, interface):
    # Netplan installs PyYAML with its own Python CLI on Ubuntu.
    import yaml
    document = yaml.safe_load(text) or {}
    wifi = document.get("network", {}).get("wifis", {}).get(interface)
    if not wifi or "access-points" not in wifi:
        return None
    if any(wifi.get(key) for key in ("addresses", "routes", "gateway4", "gateway6")):
        raise RuntimeError("This Wi-Fi interface has a custom static network. Connect it locally, then leave Wi-Fi blank.")
    del wifi["access-points"]
    return yaml.safe_dump(document, sort_keys=False)


def configure_wifi(config, stage, *, interfaces_root=Path("/sys/class/net"), etc=Path("/etc")):
    if not config["ssid"]:
        return
    interfaces = [p.name for p in interfaces_root.iterdir() if (p / "wireless").exists()]
    if len(interfaces) != 1:
        raise RuntimeError("Expected one Wi-Fi adapter. Configure Wi-Fi locally, then rerun with Wi-Fi left blank.")
    interface = interfaces[0]
    manager = shutil.which("nmcli") and run(["systemctl", "is-active", "--quiet", "NetworkManager"], check=False).returncode == 0
    if manager:
        state = run(["nmcli", "-g", "GENERAL.STATE", "device", "show", interface], capture=True).stdout.strip()
        manager = not state.startswith("10 ")  # An unmanaged Wi-Fi interface belongs to netplan/networkd.
    if manager:
        previous = run(["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", interface], capture=True).stdout.strip()
        path = etc / "NetworkManager/system-connections/swarmlink-hotspot.nmconnection"
        backup = path.read_text() if path.exists() else None
        old_uuid = re.search(r"^uuid=([a-f0-9-]{36})$", backup or "", re.M)
        profile_id = old_uuid[1] if old_uuid else str(uuid4())
        save(path, nm_profile(config, interface, profile_id))
        try:
            run(["nmcli", "connection", "load", str(path)])
            run(["nmcli", "--wait", "60", "connection", "up", "uuid", profile_id, "ifname", interface], timeout=75)
        except Exception:
            if backup is None:
                run(["nmcli", "connection", "delete", "uuid", profile_id], check=False)
                path.unlink(missing_ok=True)
            else:
                save(path, backup)
            run(["nmcli", "connection", "reload"], check=False)
            if previous and previous != "--":
                run(["nmcli", "--wait", "30", "connection", "up", "uuid", previous], check=False, timeout=40)
            raise RuntimeError("Wi-Fi connection failed; restored the previous profile. Check the hotspot name/password and signal.") from None
    elif shutil.which("netplan"):
        path = etc / "netplan/99-swarmlink-wifi.yaml"
        backup = path.read_text() if path.exists() else None
        old_files = {}
        # A later YAML file merges AP names rather than replacing them. Remove only
        # this adapter's old AP entries, keeping complete originals for rollback.
        for existing in path.parent.glob("*.yaml"):
            if existing == path:
                continue
            original = existing.read_text()
            replacement = netplan_without_old_wifi(original, interface)
            if replacement is not None:
                old_files[existing] = (original, replacement)
        backups = stage / "network-backup"
        backups.mkdir(exist_ok=True, mode=0o700)
        for existing, (original, _) in old_files.items():
            save(backups / existing.name, original)
        if backup is not None:
            save(backups / path.name, backup)
        try:
            for existing, (_, replacement) in old_files.items():
                save(existing, replacement)
            save(path, netplan_profile(config, interface))
            run(["netplan", "generate"], timeout=45)
            run(["netplan", "apply"], timeout=90)
            for _ in range(20):
                result = json.loads(run(["ip", "-j", "-4", "addr", "show", "dev", interface], capture=True).stdout)
                if any(a.get("scope") == "global" for item in result for a in item.get("addr_info", [])):
                    return
                time.sleep(3)
            raise RuntimeError("No Wi-Fi address received.")
        except Exception:
            for existing, (original, _) in old_files.items():
                save(existing, original)
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                save(path, backup)
            run(["netplan", "apply"], check=False, timeout=90)
            raise RuntimeError("Wi-Fi connection failed; restored the previous netplan file. Check the hotspot and Wi-Fi details.") from None
    else:
        raise RuntimeError("No supported Wi-Fi manager. Connect Wi-Fi locally, then leave Wi-Fi blank in the launcher.")


def required_packages(config):
    return ["acl", "dbus-user-session", "ros-jazzy-ros-base", "ros-jazzy-rclpy", "ros-jazzy-nav-msgs",
            "ros-jazzy-sensor-msgs", "ros-jazzy-ros2launch", "ros-jazzy-rosidl-runtime-py",
            "ros-jazzy-turtlebot3-bringup", "ros-jazzy-" + LIDAR_PACKAGES[config["lidar_model"]]]


def install_packages(config, stage):
    run(["apt-get", "-o", "DPkg::Lock::Timeout=300", "update"])
    candidate = run(["apt-cache", "policy", "ros-jazzy-ros-base"], capture=True).stdout
    if not re.search(r"Candidate:\s+(?!\(none\))\S+", candidate):
        run(["apt-get", "-o", "DPkg::Lock::Timeout=300", "install", "-y", "ca-certificates", "curl", "software-properties-common"])
        run(["add-apt-repository", "-y", "universe"])
        url = "https://github.com/ros-infrastructure/ros-apt-source/releases/latest"
        with urllib.request.urlopen(url, timeout=45) as response:
            version = response.url.rsplit("/", 1)[-1]
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise RuntimeError("Could not identify the official ROS apt-source release.")
        package = stage / "ros2-apt-source.deb"
        url = f"https://github.com/ros-infrastructure/ros-apt-source/releases/download/{version}/ros2-apt-source_{version}.noble_all.deb"
        run(["curl", "--fail", "--location", "--retry", "3", "--proto", "=https", "--output", str(package), url])
        run(["dpkg", "-i", str(package)])
        run(["apt-get", "-o", "DPkg::Lock::Timeout=300", "update"])
    run(["apt-get", "-o", "DPkg::Lock::Timeout=300", "install", "-y", *required_packages(config)])


def user_path(user, relative):
    home = Path(user.pw_dir).resolve()
    path = home / relative
    if not path.resolve().is_relative_to(home):
        raise ValueError("Robot installation path leaves its home directory.")
    current = home
    for part in Path(relative).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("Robot installation directories must not be symlinks.")
        if not current.exists():
            current.mkdir(mode=0o700)
            os.chown(current, user.pw_uid, user.pw_gid)
    if path.is_symlink():
        raise ValueError("Robot installation file must not be a symlink.")
    return path


def user_command(user, args, **kwargs):
    return run(["runuser", "-u", user.pw_name, "--", "env", f"HOME={user.pw_dir}",
                f"XDG_RUNTIME_DIR=/run/user/{user.pw_uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{user.pw_uid}/bus", *args], **kwargs)


def install_agent(config, files, user, stage):
    run(["usermod", "-a", "-G", "dialout", user.pw_name])
    # Give the current session serial access too; future logins inherit dialout.
    for device in ("/dev/ttyACM0", "/dev/ttyUSB0"):
        if Path(device).exists():
            run(["setfacl", "-m", f"u:{user.pw_name}:rw", device])
    run(["loginctl", "enable-linger", user.pw_name])
    run(["systemctl", "start", f"user@{user.pw_uid}.service"])
    user_command(user, ["systemctl", "--user", "stop", "swarmlink-agent", "swarmlink-bringup"], check=False)
    for name, contents in files.items():
        path = user_path(user, "SwarmLink/data-engineering/" + name)
        if path.exists():
            backup = stage / "backup" / name
            backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(path, backup)
            backup.chmod(0o600)
        save(path, contents.decode("utf-8").replace("\r\n", "\n"), 0o600 if name == ".runtime/robot.env" else 0o644)
        os.chown(path, user.pw_uid, user.pw_gid)
    root = Path(user.pw_dir) / "SwarmLink/data-engineering"
    prepare = shlex.quote(str(root / "deployment/robot/prepare-runtime.sh"))
    probe = f"source {prepare} && python3 -c 'import rclpy; from nav_msgs.msg import Odometry; from sensor_msgs.msg import LaserScan, BatteryState' && ros2 pkg prefix turtlebot3_bringup"
    user_command(user, ["bash", "-c", probe], timeout=45)
    user_command(user, ["systemctl", "--user", "daemon-reload"])
    units = [str(root / "deployment/robot" / (name + ".service")) for name in ("swarmlink-bringup", "swarmlink-agent")]
    user_command(user, ["systemctl", "--user", "enable", *units])
    user_command(user, ["systemctl", "--user", "restart", "swarmlink-bringup", "swarmlink-agent"])
    user_command(user, ["systemctl", "--user", "is-active", "swarmlink-bringup", "swarmlink-agent"])


def execute(stage):
    config = validate(json.loads((stage / "setup-config.json").read_text()))
    phase = "preflight"
    try:
        user = target_user(config)
        files = payload_files(stage / "payload.tar")
        check_payload(config, files)
        # Let the SSH caller receive launch acknowledgement before changing Wi-Fi.
        time.sleep(3)
        phase = "wifi"
        status(config, phase, "Connecting the saved hotspot profile; SSH may disconnect.")
        configure_wifi(config, stage)
        phase = "packages"
        status(config, phase, "Installing ROS 2 Jazzy and the selected TurtleBot3 driver packages.")
        install_packages(config, stage)
        phase = "services"
        status(config, phase, "Installing this robot's agent and enabling automatic startup.")
        install_agent(config, files, user, stage)
        status(config, "complete", "Agent and sensor services are enabled. The laptop launcher checks real telemetry next.")
        # Retain identity/status, but remove the extra Wi-Fi password copy after success.
        config["wifi_password"] = ""
        save(stage / "setup-config.json", json.dumps(config, indent=2) + "\n")
    except Exception as exc:
        status(config, "failed", f"Step {phase}: {exc}")
        raise


def prepare_system_directory():
    BASE.mkdir(mode=0o755, parents=True, exist_ok=True)
    if BASE.is_symlink() or BASE.stat().st_uid != 0:
        raise ValueError("The system setup directory must be owned by root.")
    BASE.chmod(0o755)


def launch(bundle):
    config = validate(json.loads((bundle / "setup-config.json").read_text()))
    user = target_user(config)
    caller = os.environ.get("SUDO_USER")
    if caller and caller != user.pw_name:
        raise ValueError("Log in as the username chosen in the setup package before running it.")
    files = payload_files(bundle / "payload.tar")
    check_payload(config, files)
    if run(["systemctl", "is-active", "--quiet", "swarmlink-setup"], check=False).returncode == 0:
        raise RuntimeError("A setup is already running. Check its status instead of starting another.")
    prepare_system_directory()
    stage = BASE / config["job_id"]
    stage.mkdir(mode=0o700)  # A job is immutable; a retry gets a new package/job ID.
    for name in ("setup-config.json", "payload.tar", "setup_robot.py"):
        source = bundle / name
        if source.is_symlink():
            raise ValueError("Setup package files must not be symlinks.")
        shutil.copyfile(source, stage / name)
        (stage / name).chmod(0o600)
    status(config, "queued", "Starting the background installer.")
    run(["systemd-run", "--unit=swarmlink-setup", "--collect", "--property=Type=exec", "--property=UMask=0077",
         "/usr/bin/python3", str(stage / "setup_robot.py"), "--run", str(stage)], timeout=30)
    # Source copies on USB may be read-only. Remove extra secrets when possible.
    try:
        (bundle / "setup-config.json").unlink()
    except OSError:
        pass
    print("Installer launched. A Wi-Fi disconnect will not stop it.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--launch", type=Path)
    action.add_argument("--run", type=Path)
    args = parser.parse_args()
    try:
        launch(args.launch.resolve()) if args.launch else execute(args.run.resolve())
        return 0
    except Exception as exc:
        print(f"SwarmLink setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
