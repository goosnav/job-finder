#!/usr/bin/env python3
"""Source CLI entrypoint; native launchers start job_finder.start directly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from job_finder.cli import app  # noqa: E402

if __name__ == "__main__":
    app()
