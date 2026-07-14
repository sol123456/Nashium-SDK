"""
Nashium Worker - Bridge between JHipster backend and the match execution engine.
"""

# Don't import from runner here to avoid circular imports
# Users should import directly:
#   from nashium.worker.runner import Worker, WorkerConfig
#   from nashium.worker.client import NashiumClient

__all__ = ["Worker", "WorkerConfig", "NashiumClient"]

def __getattr__(name):
    """Lazy imports to avoid circular dependencies."""
    if name == "Worker":
        from .runner import Worker
        return Worker
    elif name == "WorkerConfig":
        from .runner import WorkerConfig
        return WorkerConfig
    elif name == "NashiumClient":
        from .client import NashiumClient
        return NashiumClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")