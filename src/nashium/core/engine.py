from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable


@dataclass(frozen=True)
class RoundState:
    round_index: int
    my_history: tuple[int, ...]
    opponent_history: tuple[int, ...]




@runtime_checkable
class BotExecutor(Protocol):
    """Protocol for bot execution strategies."""

    def get_move(self, state: "RoundState") -> int:
        """Get the bot's move for the given state. Returns 0 if timed out."""
        ...

    @property
    def elapsed_time(self) -> float:
        """Total time spent by the bot computing moves."""
        ...

    @property
    def timed_out(self) -> bool:
        """Whether the bot has exceeded its time limit."""
        ...

    def close(self) -> None:
        """Clean up any resources."""
        ...

    def __enter__(self) -> "BotExecutor": ...
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...


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
    leaderboard_cpu_usage_samples: tuple[int, ...] = ()
    leaderboard_ram_usage_samples: tuple[int, ...] = ()




def _play_rounds_with_executors(
    submitted_executor: BotExecutor,
    leaderboard_executor: BotExecutor,
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
    submitted_executor: BotExecutor,
    leaderboard_executor: BotExecutor,
    wall_time: float,
) -> MatchSummary:
    """Build a MatchSummary from match results."""
    win_rate = submitted_wins / config.rounds if config.rounds else 0.0

    return MatchSummary(
        rounds=config.rounds,
        submitted_wins=submitted_wins,
        submitted_win_rate=win_rate,
        submitted_time_seconds=submitted_executor.elapsed_time,
        leaderboard_time_seconds=leaderboard_executor.elapsed_time,
        wall_time_seconds=wall_time,
        submitted_timed_out=submitted_executor.timed_out,
        leaderboard_timed_out=leaderboard_executor.timed_out,
    )


def run_match_with_executors(
    submitted_executor: BotExecutor,
    leaderboard_executor: BotExecutor,
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
    submitted_executor: BotExecutor,
    leaderboard_executor: BotExecutor,
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