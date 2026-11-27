import pytest

from swarmlink_data import desktop


def test_launcher_reopens_existing_live_dashboard_without_new_process(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop, "read_health", lambda: {"status": "ok"})
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *a, **k: pytest.fail("Duplicate collector spawned"))
    assert desktop.launch_dashboard(tmp_path) == "already_running"
    assert opened == [desktop.URL]
    assert not (tmp_path / ".runtime/robot-keys.json").exists()


def test_launcher_requires_existing_robot_provisioning(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "read_health", lambda: None)
    with pytest.raises(RuntimeError, match="Robot setup is missing"):
        desktop.launch_dashboard(tmp_path, open_browser=False)
    assert not (tmp_path / ".runtime/robot-keys.json").exists()


def test_launcher_uses_live_storage_handles_spaces_and_waits_before_opening(tmp_path, monkeypatch):
    root = tmp_path / "Folder With Spaces"
    runtime = root / ".runtime"
    runtime.mkdir(parents=True)
    keys = runtime / "robot-keys.json"
    keys.write_text('{"robot-01": "existing-test-token"}')
    calls = []
    health = iter([None, {"status": "ok"}])
    monkeypatch.setattr(desktop, "read_health", lambda: next(health))

    class Process:
        def poll(self):
            return None

    def start(args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    monkeypatch.setattr(desktop.subprocess, "Popen", start)
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)
    assert desktop.launch_dashboard(root) == "started"
    args, options = calls[0]
    assert args[args.index("--keys") + 1] == str(keys)
    assert args[args.index("--data") + 1] == str(runtime / "live")
    assert options["cwd"] == root
    assert "--demo" not in args
    assert opened == [desktop.URL]
    assert keys.read_text() == '{"robot-01": "existing-test-token"}'


def test_launcher_reports_child_startup_failure(tmp_path, monkeypatch):
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    (runtime / "robot-keys.json").write_text("{}")
    monkeypatch.setattr(desktop, "read_health", lambda: None)

    class FailedProcess:
        def poll(self):
            return 1

    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *a, **k: FailedProcess())
    monkeypatch.setattr(desktop.webbrowser, "open", lambda *a: pytest.fail("Opened before ready"))
    with pytest.raises(RuntimeError, match="could not start"):
        desktop.launch_dashboard(tmp_path)
