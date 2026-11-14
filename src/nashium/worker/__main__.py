"""Entry point for `python -m nashium.worker`.

The multi-worker supervisor spawns each child worker with
`[sys.executable, "-m", "nashium.worker", ...]`, so this module must exist -
without it every child dies instantly with
"No module named nashium.worker.__main__".
"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
