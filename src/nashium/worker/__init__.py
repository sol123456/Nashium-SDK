"""Nashium worker: claims matches from the server and runs them in Docker."""

__all__ = ["Worker", "WorkerConfig", "NashiumClient", "main"]


def __getattr__(name):
    if name in ("Worker", "WorkerConfig"):
        from . import runner
        return getattr(runner, name)
    if name == "NashiumClient":
        from .client import NashiumClient
        return NashiumClient
    if name == "main":
        from .cli import main
        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")