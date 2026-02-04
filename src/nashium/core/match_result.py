from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .engine import InteractionResult, MatchConfig, MatchSummary, MatchTrace


DefaultReason = Literal["cpu", "ram", "error"]


def _entropy_01(values: tuple[int, ...]) -> float | None:
    n = len(values)
    if n == 0:
        return None

    ones = sum(1 for v in values if v == 1)
    p1 = ones / n
    p0 = 1.0 - p1

    h = 0.0
    for p in (p0, p1):
        if p > 0.0:
            h -= p * math.log2(p)
    return h


def _sharpe_ratio(returns: tuple[float, ...]) -> float | None:
    n = len(returns)
    if n == 0:
        return None

    mu = sum(returns) / n

    if n < 2:
        return 0.0

    var = sum((x - mu) ** 2 for x in returns) / (n - 1)
    if var <= 0.0:
        return 0.0

    return (mu / math.sqrt(var)) * math.sqrt(n)


def _lag1_autocorr(values: tuple[float, ...]) -> float | None:
    n = len(values)
    if n < 2:
        return None

    x = values[:-1]
    y = values[1:]
    m_x = sum(x) / len(x)
    m_y = sum(y) / len(y)

    cov = sum((a - m_x) * (b - m_y) for a, b in zip(x, y))
    var_x = sum((a - m_x) ** 2 for a in x)
    var_y = sum((b - m_y) ** 2 for b in y)

    denom = math.sqrt(var_x * var_y)
    if denom <= 0.0:
        return 0.0

    return cov / denom


@dataclass(frozen=True)
class RuntimeStats:
    elapsed_time_seconds: float
    timed_out: bool
    memory_exceeded: bool
    errored: bool
    error_message: str | None
    memory_bytes_peak: int | None
    cpu_usage_samples: tuple[int, ...] = ()
    ram_usage_samples: tuple[int, ...] = ()

    @property
    def default_reason(self) -> DefaultReason | None:
        if self.memory_exceeded:
            return "ram"
        if self.timed_out:
            return "cpu"
        if self.errored:
            return "error"
        return None


@dataclass(frozen=True)
class BotStats:
    wins: int
    losses: int
    entropy: float | None
    sharpe_ratio: float | None
    return_autocorrelation: float | None


@dataclass(frozen=True)
class MatchResult:
    seed: int
    config: MatchConfig

    rounds: int
    submitted_wins: int
    submitted_win_rate: float
    result: InteractionResult
    stat_sig: bool

    submitted: RuntimeStats
    leaderboard: RuntimeStats

    wall_time_seconds: float

    submitted_moves: tuple[int, ...] = ()
    leaderboard_moves_raw: tuple[int, ...] = ()
    leaderboard_moves_effective: tuple[int, ...] = ()

    submitted_performance: tuple[int, ...] = ()
    leaderboard_output_deduced: tuple[int, ...] = ()

    submitted_stats: BotStats | None = None
    leaderboard_stats: BotStats | None = None

    def to_match_summary(self) -> MatchSummary:
        return MatchSummary(
            rounds=self.rounds,
            submitted_wins=self.submitted_wins,
            submitted_win_rate=self.submitted_win_rate,
            result=self.result,
            stat_sig=self.stat_sig,
            submitted_time_seconds=self.submitted.elapsed_time_seconds,
            leaderboard_time_seconds=self.leaderboard.elapsed_time_seconds,
            wall_time_seconds=self.wall_time_seconds,
            submitted_timed_out=self.submitted.timed_out,
            leaderboard_timed_out=self.leaderboard.timed_out,
            submitted_memory_bytes_peak=self.submitted.memory_bytes_peak,
            leaderboard_memory_bytes_peak=self.leaderboard.memory_bytes_peak,
            submitted_memory_exceeded=self.submitted.memory_exceeded,
            leaderboard_memory_exceeded=self.leaderboard.memory_exceeded,
        )

    def to_match_trace(self) -> MatchTrace:
        summary = self.to_match_summary()
        return MatchTrace(
            summary=summary,
            submitted_moves=self.submitted_moves,
            leaderboard_moves_raw=self.leaderboard_moves_raw,
            leaderboard_moves_effective=self.leaderboard_moves_effective,
            submitted_cpu_usage_samples=self.submitted.cpu_usage_samples,
            submitted_ram_usage_samples=self.submitted.ram_usage_samples,
            leaderboard_cpu_usage_samples=self.leaderboard.cpu_usage_samples,
            leaderboard_ram_usage_samples=self.leaderboard.ram_usage_samples,
        )


def build_derived_match_data(
    *,
    submitted_moves: tuple[int, ...],
    leaderboard_moves_effective: tuple[int, ...],
) -> tuple[tuple[int, ...], tuple[int, ...], BotStats, BotStats]:
    n = min(len(submitted_moves), len(leaderboard_moves_effective))
    s = submitted_moves[:n]
    l_eff = leaderboard_moves_effective[:n]

    score = tuple(1 if s_mv == l_mv else 0 for s_mv, l_mv in zip(s, l_eff))
    leaderboard_output_deduced = tuple(
        (1 - s_mv) if sc == 1 else s_mv for s_mv, sc in zip(s, score)
    )

    submitted_wins = sum(score)
    submitted_losses = n - submitted_wins

    submitted_returns = tuple((2.0 * sc) - 1.0 for sc in score)
    leaderboard_returns = tuple(-r for r in submitted_returns)

    s_stats = BotStats(
        wins=submitted_wins,
        losses=submitted_losses,
        entropy=_entropy_01(s),
        sharpe_ratio=_sharpe_ratio(submitted_returns),
        return_autocorrelation=_lag1_autocorr(submitted_returns),
    )

    l_stats = BotStats(
        wins=submitted_losses,
        losses=submitted_wins,
        entropy=_entropy_01(leaderboard_output_deduced),
        sharpe_ratio=_sharpe_ratio(leaderboard_returns),
        return_autocorrelation=_lag1_autocorr(leaderboard_returns),
    )

    return score, leaderboard_output_deduced, s_stats, l_stats
