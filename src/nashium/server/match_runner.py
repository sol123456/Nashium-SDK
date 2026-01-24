"""Server-side match runner for untrusted code."""
from __future__ import annotations

from pathlib import Path

from ..core import (
    MatchConfig,
    MatchSummary,
    MatchTrace,
    run_match_with_executors,
    run_match_trace_with_executors,
)
from .sandbox import SubprocessExecutor


def load_bot_code(path: str | Path) -> str:
    """Load bot source code from a file."""
    return Path(path).read_text()


def run_match_from_code(
    submitted_code: str,
    leaderboard_code: str,
    config: MatchConfig | None = None,
) -> MatchSummary:
    """Run a match between two bots given as source code strings."""
    config = config or MatchConfig()

    with SubprocessExecutor(submitted_code, config.max_total_time_seconds_per_bot) as submitted:
        with SubprocessExecutor(leaderboard_code, config.max_total_time_seconds_per_bot) as leaderboard:
            return run_match_with_executors(submitted, leaderboard, config)


def run_match_from_files(
    submitted_path: str | Path,
    leaderboard_path: str | Path,
    config: MatchConfig | None = None,
) -> MatchSummary:
    """Run a match between two bots given as file paths."""
    submitted_code = load_bot_code(submitted_path)
    leaderboard_code = load_bot_code(leaderboard_path)
    return run_match_from_code(submitted_code, leaderboard_code, config)


def run_match_trace_from_code(
    submitted_code: str,
    leaderboard_code: str,
    config: MatchConfig | None = None,
) -> MatchTrace:
    """Run a match with full trace between two bots given as source code."""
    config = config or MatchConfig()

    with SubprocessExecutor(submitted_code, config.max_total_time_seconds_per_bot) as submitted:
        with SubprocessExecutor(leaderboard_code, config.max_total_time_seconds_per_bot) as leaderboard:
            return run_match_trace_with_executors(submitted, leaderboard, config)