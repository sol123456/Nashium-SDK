from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .executor import BotExecutor


@dataclass(frozen=True)
class RoundState:
    round_index: int
    my_history: tuple[int, ...]
    opponent_history: tuple[int, ...]


class InteractionResult(str, Enum):
    S_LOSS = "S_LOSS"
    S_WIN = "S_WIN"
    DRAW = "DRAW"
    STAT_DRAW_S_WIN = "STAT_DRAW_S_WIN"
    STAT_DRAW_S_LOSS = "STAT_DRAW_S_LOSS"


@dataclass(frozen=True)
class MatchConfig:
    rounds: int = 10_000
    stat_sig_win_threshold: int = 5155
    max_total_time_seconds_per_bot: float = 100.0
    max_total_memory_bytes_per_bot: int | None = None


@dataclass(frozen=True)
class MatchSummary:
    rounds: int
    submitted_wins: int
    submitted_win_rate: float
    result: InteractionResult
    stat_sig: bool
    submitted_time_seconds: float
    leaderboard_time_seconds: float
    wall_time_seconds: float
    submitted_timed_out: bool = False
    leaderboard_timed_out: bool = False
    submitted_memory_bytes_peak: int | None = None
    leaderboard_memory_bytes_peak: int | None = None
    submitted_memory_exceeded: bool = False
    leaderboard_memory_exceeded: bool = False


@dataclass(frozen=True)
class MatchTrace:
    summary: MatchSummary
    submitted_moves: tuple[int, ...]
    leaderboard_moves_raw: tuple[int, ...]
    leaderboard_moves_effective: tuple[int, ...]
    submitted_cpu_usage_samples: tuple[int, ...] = ()
    submitted_ram_usage_samples: tuple[int, ...] = ()


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


def _play_rounds_with_executors(
    submitted_executor: "BotExecutor",
    leaderboard_executor: "BotExecutor",
    config: MatchConfig,
    *,
    capture_histories: bool,
) -> tuple:
    """Core game loop using executor abstraction."""
    submitted_history: list[int] = []
    leaderboard_raw_history: list[int] = []
    leaderboard_effective_history: list[int] = []
    submitted_wins = 0

    for i in range(config.rounds):
        s_state = RoundState(i, tuple(submitted_history), tuple(leaderboard_effective_history))
        l_state = RoundState(i, tuple(leaderboard_raw_history), tuple(submitted_history))

        s_move = submitted_executor.get_move(s_state)
        l_move_raw = leaderboard_executor.get_move(l_state)

        # Invert leaderboard move - makes game zero-sum
        l_move = 1 - l_move_raw

        if s_move == l_move:
            submitted_wins += 1

        submitted_history.append(s_move)
        leaderboard_raw_history.append(l_move_raw)
        leaderboard_effective_history.append(l_move)

    if capture_histories:
        return (
            submitted_wins,
            tuple(submitted_history),
            tuple(leaderboard_raw_history),
            tuple(leaderboard_effective_history),
        )
    return (submitted_wins, None, None, None)


def _build_summary(
    submitted_wins: int,
    config: MatchConfig,
    submitted_executor: "BotExecutor",
    leaderboard_executor: "BotExecutor",
    wall_time: float,
) -> MatchSummary:
    """Build a MatchSummary from match results."""
    result, stat_sig = _compute_result(submitted_wins, config)
    win_rate = submitted_wins / config.rounds if config.rounds else 0.0

    return MatchSummary(
        rounds=config.rounds,
        submitted_wins=submitted_wins,
        submitted_win_rate=win_rate,
        result=result,
        stat_sig=stat_sig,
        submitted_time_seconds=submitted_executor.elapsed_time,
        leaderboard_time_seconds=leaderboard_executor.elapsed_time,
        wall_time_seconds=wall_time,
        submitted_timed_out=submitted_executor.timed_out,
        leaderboard_timed_out=leaderboard_executor.timed_out,
    )


def run_match_with_executors(
    submitted_executor: "BotExecutor",
    leaderboard_executor: "BotExecutor",
    config: MatchConfig,
) -> MatchSummary:
    """Run a match using pre-configured executors.

    Use this for sandboxed execution with DockerExecutor.
    """
    start = time.perf_counter()

    submitted_wins, _, _, _ = _play_rounds_with_executors(
        submitted_executor, leaderboard_executor, config, capture_histories=False
    )

    return _build_summary(
        submitted_wins, config, submitted_executor, leaderboard_executor,
        time.perf_counter() - start
    )


def run_match_trace_with_executors(
    submitted_executor: "BotExecutor",
    leaderboard_executor: "BotExecutor",
    config: MatchConfig,
) -> MatchTrace:
    """Run a match with full trace using pre-configured executors."""
    start = time.perf_counter()

    submitted_wins, s_hist, l_raw, l_eff = _play_rounds_with_executors(
        submitted_executor, leaderboard_executor, config, capture_histories=True
    )

    summary = _build_summary(
        submitted_wins, config, submitted_executor, leaderboard_executor,
        time.perf_counter() - start
    )

    return MatchTrace(
        summary=summary,
        submitted_moves=s_hist or (),
        leaderboard_moves_raw=l_raw or (),
        leaderboard_moves_effective=l_eff or (),
    )


# Backward-compatible convenience functions
def run_match(submitted_bot, leaderboard_bot, config: MatchConfig) -> MatchSummary:
    """Run a match between two bot objects.

    Convenience function for local testing with trusted code.
    For sandboxed execution, use run_match_with_executors().
    """
    from .executor import LocalExecutor

    with LocalExecutor(submitted_bot, config.max_total_time_seconds_per_bot) as sub:
        with LocalExecutor(leaderboard_bot, config.max_total_time_seconds_per_bot) as lb:
            return run_match_with_executors(sub, lb, config)


def run_match_trace(submitted_bot, leaderboard_bot, config: MatchConfig) -> MatchTrace:
    """Run a match with full trace between two bot objects.

    Convenience function for local testing with trusted code.
    For sandboxed execution, use run_match_trace_with_executors().
    """
    from .executor import LocalExecutor

    with LocalExecutor(submitted_bot, config.max_total_time_seconds_per_bot) as sub:
        with LocalExecutor(leaderboard_bot, config.max_total_time_seconds_per_bot) as lb:
            return run_match_trace_with_executors(sub, lb, config)