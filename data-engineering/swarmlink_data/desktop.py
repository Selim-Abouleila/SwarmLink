"""Start or reopen this PC's live dashboard without a persistent console window."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from .lease import DirectoryLease

URL = "http://127.0.0.1:8765"


def read_health():
    # A PC-local launcher must not send its health check through an HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL + "/api/health", timeout=1) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Port 8765 is occupied by another application.") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Port 8765 did not return a SwarmLink dashboard response.") from exc
    if not isinstance(result, dict) or result.get("service") != "swarmlink-data" or result.get("mode") != "live":
        raise RuntimeError("Port 8765 is already in use by another app or a demo collector.")
    return result


def launch_dashboard(root: Path, open_browser: bool = True):
    root = root.resolve()
    runtime = root / ".runtime"
    lock_dir = runtime / "launcher"
    lock_dir.mkdir(parents=True, exist_ok=True)
    try:
        lease = DirectoryLease(lock_dir)
    except RuntimeError:
        # Another double-click is already starting the collector.
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            health = read_health()
            if health and health.get("status") == "ok":
                if open_browser:
                    webbrowser.open(URL)
                return "already_running"
            time.sleep(.25)
        raise RuntimeError("The dashboard is still starting. Check .runtime/dashboard.log.")
    try:
        health = read_health()
        if health and health.get("status") == "stopping":
            deadline = time.monotonic() + 35
            while health and time.monotonic() < deadline:
                time.sleep(.25)
                health = read_health()
            if health:
                raise RuntimeError("The previous dashboard is still shutting down. Try again shortly.")
        if health and health.get("status") == "ok":
            if open_browser:
                webbrowser.open(URL)
            return "already_running"
        keys = runtime / "robot-keys.json"
        if not keys.is_file():
            raise RuntimeError("Robot setup is missing: .runtime/robot-keys.json was not found. See ../documentation/LIVE_ROBOT.md.")
        # Import here so an already-running dashboard can still be opened.
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Install this project's Python dependencies first; see README.md.") from exc
        log_path = runtime / "dashboard.log"
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        # pythonw can leave sys.stderr unset; the collector needs real log streams.
        interpreter = Path(sys.executable)
        if os.name == "nt" and interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                [str(interpreter), "-m", "swarmlink_data", "serve", "--host", "0.0.0.0", "--port", "8765",
                 "--keys", str(keys), "--robots", "1", "--data", str(runtime / "live")],
                cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=flags, env={**os.environ, "PYTHONUTF8": "1"},
            )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"The collector could not start. Details are in {log_path}")
            health = read_health()
            if health and health.get("status") == "ok":
                if open_browser:
                    webbrowser.open(URL)
                return "started"
            time.sleep(.25)
        raise RuntimeError(f"Startup is taking longer than expected. Details are in {log_path}")
    finally:
        lease.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Start or open the SwarmLink dashboard")
    parser.add_argument("--no-browser", action="store_true", help="Start/check the collector without opening a tab")
    args = parser.parse_args()
    try:
        launch_dashboard(Path(__file__).resolve().parents[1], open_browser=not args.no_browser)
        return 0
    except Exception as exc:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "SwarmLink dashboard could not start", 0x10)
        elif sys.stderr:
            print(str(exc), file=sys.stderr)
        return 1
