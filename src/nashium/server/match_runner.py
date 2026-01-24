"""Server-side match runner for untrusted code."""
from __future__ import annotations

import time
from pathlib import Path

from ..core import (
    MatchConfig,
    MatchSummary,
    MatchTrace,
    RoundState,
    InteractionResult,
)
from .sandbox import SubprocessExecutor


def load_bot_code(path: str | Path) -> str:
    """Load bot source code from a file."""
    return Path(path).read_text()


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


def run_match_from_code(
    submitted_code: str,
    leaderboard_code: str,
    config: MatchConfig | None = None,
) -> MatchSummary:
    """Run a match between two bots given as source code strings."""
    config = config or MatchConfig()
    start = time.perf_counter()

    with SubprocessExecutor(submitted_code, config.max_total_time_seconds_per_bot) as submitted:
        with SubprocessExecutor(leaderboard_code, config.max_total_time_seconds_per_bot) as leaderboard:
            submitted_wins = 0
            last_submitted_move: int | None = None
            last_leaderboard_effective: int | None = None

            for i in range(config.rounds):
                # Each bot receives opponent's last move (inverted for leaderboard)
                s_move = submitted.get_move(last_leaderboard_effective)
                l_move_raw = leaderboard.get_move(last_submitted_move)

                # Invert leaderboard move
                l_move = 1 - l_move_raw

                if s_move == l_move:
                    submitted_wins += 1

                last_submitted_move = s_move
                last_leaderboard_effective = l_move

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
    start = time.perf_counter()

    with SubprocessExecutor(submitted_code, config.max_total_time_seconds_per_bot) as submitted:
        with SubprocessExecutor(leaderboard_code, config.max_total_time_seconds_per_bot) as leaderboard:
            submitted_wins = 0
            submitted_history: list[int] = []
            leaderboard_raw_history: list[int] = []
            leaderboard_effective_history: list[int] = []

            last_submitted_move: int | None = None
            last_leaderboard_effective: int | None = None

            for i in range(config.rounds):
                s_move = submitted.get_move(last_leaderboard_effective)
                l_move_raw = leaderboard.get_move(last_submitted_move)
                l_move = 1 - l_move_raw

                if s_move == l_move:
                    submitted_wins += 1

                submitted_history.append(s_move)
                leaderboard_raw_history.append(l_move_raw)
                leaderboard_effective_history.append(l_move)

                last_submitted_move = s_move
                last_leaderboard_effective = l_move

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
        submitted_moves=tuple(submitted_history),
        leaderboard_moves_raw=tuple(leaderboard_raw_history),
        leaderboard_moves_effective=tuple(leaderboard_effective_history),
    )