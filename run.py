"""Development entry point used by `npm run dev`."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from furniture_layout.web import main


if __name__ == "__main__":
    raise SystemExit(main())
