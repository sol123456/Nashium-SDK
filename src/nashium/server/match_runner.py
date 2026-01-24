"""
Server-side match runner for untrusted code.

Provides an API similar to the CLI's local runner, but executes
bot code in isolated subprocesses.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from ..core import (
    MatchConfig,
    MatchSummary,
    MatchTrace,
    InteractionResult,
)
from .sandbox import SubprocessExecutor


def load_bot_code(path: str | Path) -> str:
    """Load bot source code from a file."""
    return Path(path).read_text()


def load_bot_from_file(
    path: str | Path,
    seed: int | None = None,
    *,
    time_limit: float = 100.0,
    move_timeout: float = 5.0,
) -> SubprocessExecutor:
    """
    Load a bot from a file path into a subprocess executor.

    This mirrors the CLI's load_bot_from_file() but returns an executor
    suitable for sandboxed execution.

    Args:
        path: Path to the bot source file
        seed: Optional seed passed to the bot constructor
        time_limit: Total time allowed for all moves
        move_timeout: Max time for a single move before kill

    Returns:
        A SubprocessExecutor ready to play

    Example:
        with load_bot_from_file("my_bot.py", seed=42) as bot:
            move = bot.get_move(None)
    """
    code = load_bot_code(path)
    return SubprocessExecutor(
        code,
        time_limit=time_limit,
        move_timeout=move_timeout,
        seed=seed,
    )


def load_bot_from_code(
    code: str,
    seed: int | None = None,
    *,
    time_limit: float = 100.0,
    move_timeout: float = 5.0,
) -> SubprocessExecutor:
    """
    Load a bot from source code string into a subprocess executor.

    Args:
        code: Bot source code as a string
        seed: Optional seed passed to the bot constructor
        time_limit: Total time allowed for all moves
        move_timeout: Max time for a single move before kill

    Returns:
        A SubprocessExecutor ready to play
    """
    return SubprocessExecutor(
        code,
        time_limit=time_limit,
        move_timeout=move_timeout,
        seed=seed,
    )


def _compute_result(submitted_wins: int, config: MatchConfig) -> tuple[InteractionResult, bool]:
    """Compute match result and statistical significance."""
    stat_sig = (
        submitted_wins >= config.stat_sig_win_threshold
        or submitted_wins <= (config.rounds - config.stat_sig_win_threshold)
    )

    if submitted_wins >= config.stat_sig_win_threshold:
        result = InteractionResult.S_WIN
    elif submitted_wins <= (config.rounds - config.stat_sig_win_threshold):
        result = InteractionResult.S_LOSS
    elif submitted_wins == config.rounds // 2:
        result = InteractionResult.DRAW
    elif submitted_wins > config.rounds // 2:
        result = InteractionResult.STAT_DRAW_S_WIN
    else:
        result = InteractionResult.STAT_DRAW_S_LOSS

    return result, stat_sig


def _run_match_loop(
    submitted: SubprocessExecutor,
    leaderboard: SubprocessExecutor,
    config: MatchConfig,
    *,
    capture_history: bool = False,
) -> tuple[int, list[int] | None, list[int] | None, list[int] | None]:
    """Core match loop. Returns (wins, s_hist, l_raw, l_eff)."""
    submitted_wins = 0
    last_submitted_move: int | None = None
    last_leaderboard_effective: int | None = None

    if capture_history:
        submitted_history: list[int] = []
        leaderboard_raw_history: list[int] = []
        leaderboard_effective_history: list[int] = []
    else:
        submitted_history = None
        leaderboard_raw_history = None
        leaderboard_effective_history = None

    for i in range(config.rounds):
        s_move = submitted.get_move(last_leaderboard_effective)
        l_move_raw = leaderboard.get_move(last_submitted_move)
        l_move = 1 - l_move_raw

        if s_move == l_move:
            submitted_wins += 1

        if capture_history:
            submitted_history.append(s_move)
            leaderboard_raw_history.append(l_move_raw)
            leaderboard_effective_history.append(l_move)

        last_submitted_move = s_move
        last_leaderboard_effective = l_move

    return submitted_wins, submitted_history, leaderboard_raw_history, leaderboard_effective_history


def run_match(
    submitted: SubprocessExecutor,
    leaderboard: SubprocessExecutor,
    config: MatchConfig | None = None,
) -> MatchSummary:
    """
    Run a match between two executor-wrapped bots.

    This is the low-level API when you've already created executors.

    Example:
        with load_bot_from_file("submitted.py", seed=123) as sub:
            with load_bot_from_file("opponent.py", seed=123) as opp:
                result = run_match(sub, opp, MatchConfig())
    """
    config = config or MatchConfig()
    start = time.perf_counter()

    submitted_wins, _, _, _ = _run_match_loop(
        submitted, leaderboard, config, capture_history=False
    )

    wall_time = time.perf_counter() - start
    result, stat_sig = _compute_result(submitted_wins, config)
    win_rate = submitted_wins / config.rounds if config.rounds else 0.0

    return MatchSummary(
        rounds=config.rounds,
        submitted_wins=submitted_wins,
        submitted_win_rate=win_rate,
        result=result,
        stat_sig=stat_sig,
        submitted_time_seconds=submitted.elapsed_time,
        leaderboard_time_seconds=leaderboard.elapsed_time,
        wall_time_seconds=wall_time,
        submitted_timed_out=submitted.timed_out,
        leaderboard_timed_out=leaderboard.timed_out,
    )


def run_match_trace(
    submitted: SubprocessExecutor,
    leaderboard: SubprocessExecutor,
    config: MatchConfig | None = None,
) -> MatchTrace:
    """Run a match with full move history."""
    config = config or MatchConfig()
    start = time.perf_counter()

    submitted_wins, s_hist, l_raw, l_eff = _run_match_loop(
        submitted, leaderboard, config, capture_history=True
    )

    wall_time = time.perf_counter() - start
    result, stat_sig = _compute_result(submitted_wins, config)
    win_rate = submitted_wins / config.rounds if config.rounds else 0.0

    summary = MatchSummary(
        rounds=config.rounds,
        submitted_wins=submitted_wins,
        submitted_win_rate=win_rate,
        result=result,
        stat_sig=stat_sig,
        submitted_time_seconds=submitted.elapsed_time,
        leaderboard_time_seconds=leaderboard.elapsed_time,
        wall_time_seconds=wall_time,
        submitted_timed_out=submitted.timed_out,
        leaderboard_timed_out=leaderboard.timed_out,
    )

    return MatchTrace(
        summary=summary,
        submitted_moves=tuple(s_hist or []),
        leaderboard_moves_raw=tuple(l_raw or []),
        leaderboard_moves_effective=tuple(l_eff or []),
    )


# Convenience functions that handle executor lifecycle

def run_match_from_code(
    submitted_code: str,
    leaderboard_code: str,
    config: MatchConfig | None = None,
    *,
    seed: int | None = None,
) -> MatchSummary:
    """
    Run a match between two bots given as source code strings.

    Args:
        submitted_code: Source code for the submitted bot
        leaderboard_code: Source code for the leaderboard bot
        config: Match configuration
        seed: Optional seed passed to both bot constructors
    """
    config = config or MatchConfig()

    with load_bot_from_code(submitted_code, seed=seed, time_limit=config.max_total_time_seconds_per_bot) as submitted:
        with load_bot_from_code(leaderboard_code, seed=seed, time_limit=config.max_total_time_seconds_per_bot) as leaderboard:
            return run_match(submitted, leaderboard, config)


def run_match_from_files(
    submitted_path: str | Path,
    leaderboard_path: str | Path,
    config: MatchConfig | None = None,
    *,
    seed: int | None = None,
) -> MatchSummary:
    """
    Run a match between two bots given as file paths.

    Args:
        submitted_path: Path to submitted bot source file
        leaderboard_path: Path to leaderboard bot source file
        config: Match configuration
        seed: Optional seed passed to both bot constructors
    """
    config = config or MatchConfig()

    with load_bot_from_file(submitted_path, seed=seed, time_limit=config.max_total_time_seconds_per_bot) as submitted:
        with load_bot_from_file(leaderboard_path, seed=seed, time_limit=config.max_total_time_seconds_per_bot) as leaderboard:
            return run_match(submitted, leaderboard, config)


def run_match_trace_from_code(
    submitted_code: str,
    leaderboard_code: str,
    config: MatchConfig | None = None,
    *,
    seed: int | None = None,
) -> MatchTrace:
    """Run a match with full trace between two bots given as source code."""
    config = config or MatchConfig()

    with load_bot_from_code(submitted_code, seed=seed, time_limit=config.max_total_time_seconds_per_bot) as submitted:
        with load_bot_from_code(leaderboard_code, seed=seed, time_limit=config.max_total_time_seconds_per_bot) as leaderboard:
            return run_match_trace(submitted, leaderboard, config)


def run_match_trace_from_files(
    submitted_path: str | Path,
    leaderboard_path: str | Path,
    config: MatchConfig | None = None,
    *,
    seed: int | None = None,
) -> MatchTrace:
    """Run a match with full trace between two bots given as file paths."""
    config = config or MatchConfig()

    with load_bot_from_file(submitted_path, seed=seed, time_limit=config.max_total_time_seconds_per_bot) as submitted:
        with load_bot_from_file(leaderboard_path, seed=seed, time_limit=config.max_total_time_seconds_per_bot) as leaderboard:
            return run_match_trace(submitted, leaderboard, config)