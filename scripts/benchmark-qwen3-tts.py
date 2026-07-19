#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from voice_app.tts_benchmark import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
