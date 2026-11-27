from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="SwarmLink local data pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the API and live dashboard")
    serve.add_argument("--demo", action="store_true")
    serve.add_argument("--robots", type=int, default=4)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--data", type=Path)
    serve.add_argument("--keys", type=Path)
    serve.add_argument("--discovery-port", type=int, default=8766, help="Authenticated LAN discovery UDP port; 0 disables it")
    keys = commands.add_parser("keys", help="Generate per-robot credentials without printing them")
    keys.add_argument("--robots", type=int, default=4)
    keys.add_argument("--output", type=Path, default=Path("secrets.json"))
    export = commands.add_parser("export", help="Export an experiment to JSONL, CSV, Parquet and KPIs")
    export.add_argument("--data", type=Path, default=Path(".runtime/live"))
    export.add_argument("--run", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--no-parquet", action="store_true")
    replay = commands.add_parser("replay", help="Idempotently ingest event JSONL or a raw receipt journal")
    replay.add_argument("file", type=Path)
    replay.add_argument("--collector", default="http://127.0.0.1:8765")
    replay.add_argument("--robot-id", required=True)
    args = parser.parse_args()
    if args.command == "serve":
        if not 1 <= args.robots <= 100:
            parser.error("robots must be between 1 and 100")
        if not 0 <= args.discovery_port <= 65535:
            parser.error("discovery-port must be between 0 and 65535")
        if args.demo and args.host not in ("127.0.0.1", "localhost", "::1"):
            parser.error("The anonymous demo binds only to loopback; use live mode with keys for robots")
        from .app import create_app
        import uvicorn
        tokens = json.loads(args.keys.read_text()) if args.keys else None
        if not args.demo and (not tokens or any(not isinstance(v, str) or len(v) < 24 for v in tokens.values())):
            parser.error("Live mode requires --keys with a strong token for each robot. Run the keys command.")
        data = args.data or Path(".runtime/demo" if args.demo else ".runtime/live")
        discovery_port = args.discovery_port if not args.demo and ":" not in args.host else None
        def request_shutdown():
            server.should_exit = True

        app = create_app(data, args.demo, args.robots, tokens, discovery_port, args.port, args.host,
                         on_shutdown=request_shutdown)
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning",
                                              proxy_headers=False, timeout_graceful_shutdown=30))
        server.run()
    elif args.command == "keys":
        if args.output.exists():
            parser.error("Credential file already exists; it was not overwritten")
        if not 1 <= args.robots <= 100:
            parser.error("robots must be between 1 and 100")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({f"robot-{i:02d}": secrets.token_urlsafe(32) for i in range(1, args.robots + 1)}, stream, indent=2)
        print(f"Created {args.robots} credentials in {args.output}; keep this file out of Git.")
    elif args.command == "export":
        from .export import write_export
        from .store import Store
        store = Store(args.data)
        try:
            print(json.dumps(write_export(store, args.run, args.output, not args.no_parquet), indent=2))
        finally:
            store.close()
    elif args.command == "replay":
        from .edge import send_batch
        from .store import reject_constant
        counts = {"accepted": 0, "duplicate": 0, "rejected": 0, "skipped": 0, "malformed": 0}
        with args.file.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line, parse_constant=reject_constant)
                    if isinstance(row, dict) and "raw_json" in row:
                        row = json.loads(row["raw_json"], parse_constant=reject_constant)
                    if not isinstance(row, dict):
                        raise ValueError("Expected event object")
                except (ValueError, TypeError):
                    counts["malformed"] += 1
                    continue
                if row.get("robot_id") != args.robot_id:
                    counts["skipped"] += 1
                    continue
                results = send_batch(args.collector, args.robot_id, os.environ.get("SWARMLINK_ROBOT_TOKEN", ""), [row])
                if len(results) != 1 or results[0].get("event_id") != row.get("event_id") or results[0].get("status") not in ("accepted", "duplicate", "rejected"):
                    raise ValueError("Invalid acknowledgement; replay input remains unchanged")
                result = results[0]
                counts[result["status"]] += 1
        print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
