"""Interactive entry point; runs on the laptop, never on the robot."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swarmlink_data.provisioning import main

if __name__ == "__main__":
    raise SystemExit(main())
