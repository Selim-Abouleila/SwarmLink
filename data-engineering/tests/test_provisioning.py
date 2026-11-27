import importlib.util
import io
import itertools
import json
import shlex
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from swarmlink_data import provisioning as host

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("robot_installer", ROOT / "deployment/robot/setup_robot.py")
robot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(robot)


@pytest.fixture
def config():
    return {"job_id": "a" * 32, "username": "turtle", "robot_id": "robot-02", "run_id": "hotspot-live",
            "ros_domain": 31, "model": "burger", "lidar_model": "LDS-01", "battery_scale": "percent",
            "machine_id": "b" * 32, "ssid": "Selim's iPhone", "wifi_password": 'a test$()"password'}


@pytest.fixture
def package(tmp_path, config, monkeypatch):
    root = tmp_path / "Project With Spaces"
    for name in robot.RUNTIME_FILES - {".runtime/robot.env"} | {
        "scripts/package-robot.py", "deployment/robot/setup_robot.py", "deployment/robot/SETUP-ROBOT.sh"
    }:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    (root / ".runtime").mkdir()
    (root / ".runtime/robot-keys.json").write_text(json.dumps({
        "robot-01": "one-test-token-should-not-be-shared", "robot-02": "two-test-token-for-this-robot-only"}))
    monkeypatch.setattr(host, "private_directory", lambda path: path.mkdir(parents=True, exist_ok=True))
    return root, host.make_package(root, config)


def test_generated_bundle_contains_only_selected_key_and_matching_config(package, config):
    root, folder = package
    files = robot.payload_files(folder / "payload.tar")
    robot.check_payload(config, files)
    contents = b"".join(files.values())
    assert b"two-test-token-for-this-robot-only" in contents
    assert b"one-test-token-should-not-be-shared" not in contents
    assert config["wifi_password"].encode() not in contents
    assert json.loads((folder / "setup-config.json").read_text())["ssid"] == "Selim's iPhone"
    assert (root / ".runtime/robot-keys.json").exists()


@pytest.mark.parametrize("patch", [
    {"username": "root; reboot"}, {"robot_id": "../../robot"}, {"ros_domain": 233},
    {"wifi_password": "bad\npassword"}, {"ssid": "a" * 33}, {"model": "unknown"},
    {"machine_id": "wrong"}, {"job_id": "../escape"},
])
def test_invalid_setup_input_fails_before_changes(config, patch):
    with pytest.raises(ValueError):
        robot.validate({**config, **patch})


def test_shell_characters_in_hotspot_are_data_not_commands(config):
    config.update(ssid="Lab's $(touch BAD); WiFi", wifi_password='"$;\\ long password')
    robot.validate(config)
    profile = robot.nm_profile(config, "wlan0", "uuid")
    assert 'ssid=Lab\'s\\s$(touch\\sBAD);\\sWiFi\n' in profile
    netplan = json.loads(robot.netplan_profile(config, "wlan0"))
    assert netplan["network"]["wifis"]["wlan0"]["access-points"] == {config["ssid"]: {"password": config["wifi_password"]}}


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE), ("agents/ros2_agent.py", tarfile.SYMTYPE)])
def test_unsafe_payload_is_rejected_before_extraction(tmp_path, name, kind):
    path = tmp_path / "bad.tar"
    with tarfile.open(path, "w") as archive:
        entry = tarfile.TarInfo(name)
        entry.type, entry.linkname = kind, "/etc/passwd"
        archive.addfile(entry, io.BytesIO(b""))
    with pytest.raises(ValueError, match="Unsafe"):
        robot.payload_files(path)


def test_assignment_blocks_cross_robot_key_reuse_and_can_resume_same_robot():
    registry = {"robot-02": {"machine_id": "a" * 32, "status": "prepared"}}
    with pytest.raises(ValueError, match="reserved"):
        host.check_assignment("robot-02", {"machine_id": "b" * 32}, registry, set())
    with pytest.raises(ValueError, match="already configured"):
        host.check_assignment("robot-02", {"machine_id": "a" * 32, "robot_id": "robot-01"}, registry, set())
    with pytest.raises(ValueError, match="already sent"):
        host.check_assignment("robot-01", None, {}, {"robot-01"})
    host.check_assignment("robot-02", {"machine_id": "a" * 32}, registry, {"robot-02"})
    usb = {"robot-03": {"machine_id": None, "status": "prepared"}}
    with pytest.raises(ValueError, match="reserved"):
        host.check_assignment("robot-03", None, usb, set())
    host.check_assignment("robot-03", None, usb, set(), same_usb_robot=True)


def test_preflight_ssh_quotes_paths_and_only_reads_nonsecret_identity(tmp_path, monkeypatch):
    def execute(args, **kwargs):
        assert "StrictHostKeyChecking=ask" in args
        assert any(arg.startswith('UserKnownHostsFile="') and "Project With Spaces" in arg for arg in args)
        command = shlex.split(args[-1])
        assert command[:2] == ["python3", "-c"]
        compile(command[2], "remote probe", "exec")
        return SimpleNamespace(stdout=json.dumps({"machine_id": "b" * 32, "os": {"ID": "ubuntu", "VERSION_ID": "24.04"}}))
    monkeypatch.setattr(host.subprocess, "run", execute)
    assert host.remote_identity(tmp_path / "Project With Spaces", "turtle", "172.20.10.3")["machine_id"] == "b" * 32
    with pytest.raises(ValueError):
        host.ssh_target("turtle", "-oProxyCommand=bad")
    assert host.ssh_target("turtle", "fe80::abcd%6", scp=True) == "turtle@[fe80::abcd%6]"


def test_nm_failure_restores_old_profile_and_does_not_log_password(tmp_path, config, monkeypatch):
    interfaces = tmp_path / "net"
    (interfaces / "wlan0/wireless").mkdir(parents=True)
    etc = tmp_path / "etc"
    folder = etc / "NetworkManager/system-connections"
    folder.mkdir(parents=True)
    profile = folder / "swarmlink-hotspot.nmconnection"
    original = "[connection]\nuuid=12345678-1234-1234-1234-123456789012\n"
    profile.write_text(original)
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        assert config["wifi_password"] not in " ".join(args)
        if "GENERAL.STATE" in args:
            return SimpleNamespace(stdout="100 (connected)", returncode=0)
        if "GENERAL.CON-UUID" in args:
            return SimpleNamespace(stdout="old-active-uuid", returncode=0)
        if "up" in args and "old-active-uuid" not in args:
            raise RuntimeError("wrong password")
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(robot.shutil, "which", lambda name: "/usr/bin/nmcli")
    monkeypatch.setattr(robot, "run", command)
    with pytest.raises(RuntimeError, match="restored"):
        robot.configure_wifi(config, tmp_path, interfaces_root=interfaces, etc=etc)
    assert profile.read_text() == original
    assert calls[-1][-1] == "old-active-uuid"


def test_netplan_changes_only_selected_wifi_access_points(config):
    original = {"network": {"version": 2, "ethernets": {"eth0": {"dhcp4": True}},
                "wifis": {"wlan0": {"dhcp4": True, "access-points": {"old": {"password": "old-private-key"}}}}}}
    import yaml
    changed = yaml.safe_load(robot.netplan_without_old_wifi(json.dumps(original), "wlan0"))
    assert changed["network"]["ethernets"] == original["network"]["ethernets"]
    assert changed["network"]["wifis"]["wlan0"] == {"dhcp4": True}
    assert original["network"]["wifis"]["wlan0"]["access-points"]


def test_netplan_failure_restores_original_files(tmp_path, config, monkeypatch):
    interfaces = tmp_path / "net"
    (interfaces / "wlan0/wireless").mkdir(parents=True)
    etc = tmp_path / "etc"
    (etc / "netplan").mkdir(parents=True)
    existing = etc / "netplan/50-cloud-init.yaml"
    original = "network:\n  version: 2\n  ethernets:\n    eth0:\n      dhcp4: true\n  wifis:\n    wlan0:\n      dhcp4: true\n      access-points:\n        OldNetwork:\n          password: old-password\n"
    existing.write_text(original, newline="\n")
    before = existing.read_bytes()
    monkeypatch.setattr(robot.shutil, "which", lambda name: "/usr/sbin/netplan" if name == "netplan" else None)
    def command(args, **kwargs):
        if args == ["netplan", "generate"]:
            raise RuntimeError("netplan rejected config")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(robot, "run", command)
    with pytest.raises(RuntimeError, match="restored"):
        robot.configure_wifi(config, tmp_path, interfaces_root=interfaces, etc=etc)
    assert existing.read_bytes() == before
    assert not (etc / "netplan/99-swarmlink-wifi.yaml").exists()
    assert (tmp_path / "network-backup/50-cloud-init.yaml").read_bytes() == before


def test_background_launch_is_detached_and_does_not_start_install_in_ssh(package, config, tmp_path, monkeypatch):
    _, folder = package
    base = tmp_path / "system-state"
    monkeypatch.setattr(robot, "BASE", base)
    monkeypatch.setattr(robot, "prepare_system_directory", lambda: base.mkdir())
    monkeypatch.setattr(robot, "target_user", lambda _: SimpleNamespace(pw_name="turtle"))
    monkeypatch.setenv("SUDO_USER", "turtle")
    calls = []
    monkeypatch.setattr(robot, "run", lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=3 if args[0] == "systemctl" else 0))
    robot.launch(folder)
    assert calls[-1][0] == "systemd-run"
    assert "--property=Type=exec" in calls[-1]
    assert (base / config["job_id"] / "setup-config.json").is_file()
    assert not (folder / "setup-config.json").exists()
    assert json.loads((base / "status.json").read_text())["phase"] == "queued"


def test_wifi_failure_stops_before_install_and_reports_actionable_step(package, config, tmp_path, monkeypatch):
    _, folder = package
    monkeypatch.setattr(robot, "BASE", tmp_path)
    monkeypatch.setattr(robot, "target_user", lambda _: object())
    monkeypatch.setattr(robot.time, "sleep", lambda _: None)
    monkeypatch.setattr(robot, "configure_wifi", lambda *a: (_ for _ in ()).throw(RuntimeError("Hotspot not found")))
    monkeypatch.setattr(robot, "install_packages", lambda *a: pytest.fail("Installed after Wi-Fi failed"))
    with pytest.raises(RuntimeError, match="Hotspot"):
        robot.execute(folder)
    result = json.loads((tmp_path / "status.json").read_text())
    assert result["phase"] == "failed" and "Step wifi" in result["message"]
    assert config["wifi_password"] not in json.dumps(result)


def test_success_requires_actual_live_telemetry(monkeypatch, capsys):
    snapshot = {"run_id": "hotspot-live", "robots": [{"robot_id": "robot-02", "source": "ros2", "status": "online",
        "receipt_age_s": 0, "payload": {"pose": {"x_m": 0}, "scan": {"ranges_m": []}, "battery_pct": 0}}]}
    class Opener:
        def open(self, *a, **k):
            return io.BytesIO(json.dumps(snapshot).encode())
    monkeypatch.setattr(host.urllib.request, "build_opener", lambda *a: Opener())
    assert host.wait_for_robot("robot-02", "hotspot-live", timeout=1)
    assert "SUCCESS" in capsys.readouterr().out


@pytest.mark.parametrize("status,pose", [("stale", {"x_m": 0}), ("online", None)])
def test_stale_or_missing_sensor_data_is_not_reported_as_success(status, pose, monkeypatch, capsys):
    snapshot = {"run_id": "hotspot-live", "robots": [{"robot_id": "robot-02", "source": "ros2", "status": status,
        "receipt_age_s": 0, "payload": {"pose": pose, "scan": {"ranges_m": []}, "battery_pct": 50}}]}
    class Opener:
        def open(self, *a, **k):
            return io.BytesIO(json.dumps(snapshot).encode())
    clock = itertools.count(step=5)
    monkeypatch.setattr(host.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(host.time, "sleep", lambda _: None)
    monkeypatch.setattr(host.urllib.request, "build_opener", lambda *a: Opener())
    assert not host.wait_for_robot("robot-02", "hotspot-live", timeout=100)
    assert "SUCCESS" not in capsys.readouterr().out


def test_guided_ssh_flow_selects_unused_id_and_preserves_existing_keys(package, monkeypatch):
    root, _ = package
    original_keys = (root / ".runtime/robot-keys.json").read_bytes()
    answers = iter(["1", "turtle", "172.20.10.3", "", "", "", "", "", "Student hotspot", "yes"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(host.getpass, "getpass", lambda _: "private-test-wifi")
    monkeypatch.setattr(host.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(host, "remote_identity", lambda *a: {"machine_id": "c" * 32, "robot_id": None})
    monkeypatch.setattr(host, "known_robots", lambda _: {"robot-01"})
    monkeypatch.setattr(host, "launch_dashboard", lambda *a, **k: "already_running")
    deployed = []
    def deploy(_root, folder, username, address):
        config = json.loads((folder / "setup-config.json").read_text())
        robot.check_payload(config, robot.payload_files(folder / "payload.tar"))
        deployed.append((folder, config, username, address))
    monkeypatch.setattr(host, "deploy_ssh", deploy)
    monkeypatch.setattr(host, "wait_for_robot", lambda robot_id, run_id, **kwargs: robot_id == "robot-02" and run_id == "hotspot-live")
    host.wizard(root)
    folder, config, username, address = deployed[0]
    assert (username, address) == ("turtle", "172.20.10.3")
    assert config["ros_domain"] == 31
    assert not (folder / "setup-config.json").exists()
    assert (root / ".runtime/robot-keys.json").read_bytes() == original_keys
    registry = json.loads((root / ".runtime/setup/robots.json").read_text())
    assert registry["robot-02"]["status"] == "verified"
    assert "private-test-wifi" not in json.dumps(registry)


@pytest.mark.parametrize("lidar,package", [("LDS-01", "hls-lfcd-lds-driver"), ("LDS-02", "ld08-driver"), ("LDS-03", "coin-d4-driver")])
def test_selected_hardware_controls_driver_install(config, lidar, package):
    packages = robot.required_packages({**config, "lidar_model": lidar})
    assert "ros-jazzy-" + package in packages
    assert "ros-jazzy-turtlebot3-bringup" in packages
