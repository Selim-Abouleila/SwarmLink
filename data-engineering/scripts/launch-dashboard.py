"""Entry point used by Start Dashboard.exe."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swarmlink_data.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
