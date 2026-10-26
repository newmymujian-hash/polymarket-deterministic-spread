#!/usr/bin/env python3
"""Entry point that works without installing the package.

Windows:  py run.py scan --once
Linux/macOS: python3 run.py scan --once
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ds_harvest.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
