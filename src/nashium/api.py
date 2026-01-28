from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from .cli.backend import get_backend
from .core.engine import MatchConfig
from .core.match_result import MatchResult
from .core.util import stable_seed


_CREATE_BOT_SHIM = """

def create_bot(seed: int):
    cls = globals().get("Bot")
    if cls is None:
        for name, obj in globals().items():
            if (
                isinstance(obj, type)
                and callable(getattr(obj, "move", None))
                and name not in ("RoundState",)
            ):
                cls = obj
                break

    if cls is None:
        raise ValueError("No bot class found in source code")

    try:
        return cls(seed=seed)
    except TypeError:
        try:
            return cls(seed)
        except TypeError:
            return cls()
"""


def _ensure_create_bot(code: str) -> str:
    if "def create_bot" in code:
        return code
    return code + _CREATE_BOT_SHIM


def run_match_result_from_code_strings(
    submitted_code: str,
    leaderboard_code: str,
    *,
    seed: int | None = None,
    config: MatchConfig | None = None,
    sandbox: bool = True,
    docker: bool = False,
    capture_history: bool = True,
) -> MatchResult:
    if config is None:
        config = MatchConfig()

    if seed is None:
        seed = stable_seed(submitted_code.encode("utf-8"), leaderboard_code.encode("utf-8"))

    backend = get_backend(sandbox=sandbox, docker=docker)

    with TemporaryDirectory(prefix="nashium_bots_") as tmp:
        a = Path(tmp) / "submitted.py"
        b = Path(tmp) / "leaderboard.py"

        a.write_text(_ensure_create_bot(submitted_code), encoding="utf-8")
        b.write_text(_ensure_create_bot(leaderboard_code), encoding="utf-8")

        return backend.run_match_result_between_files(
            a,
            b,
            seed,
            config,
            capture_history=capture_history,
        )
