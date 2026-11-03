#!/usr/bin/env python3
"""Run directly from a checkout without installing dependencies."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from local_agent_room.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
