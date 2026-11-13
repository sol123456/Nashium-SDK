"""Multi-worker supervisor for the Nashium SDK.

``nashium farm`` starts N ``nashium.worker`` child processes, keeps one colour
prefixed console stream per worker, writes one log file per worker, restarts
workers that die, and drains in-flight matches on Ctrl-C.

Imports are lazy so that ``import nashium.farm.supervisor`` does not drag in the
CLI layer.
"""

from __future__ import annotations

import importlib

__all__ = ["main", "build_parser"]


def __getattr__(name):
    if name in ("main", "build_parser"):
        module = importlib.import_module(".main", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
