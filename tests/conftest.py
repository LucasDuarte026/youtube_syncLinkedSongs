"""Pytest configuration ensuring youtubeMusic_editor and Maestro root are importable."""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
MAESTRO_ROOT = PROJECT_DIR.parent.parent

for p in (str(PROJECT_DIR), str(MAESTRO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
