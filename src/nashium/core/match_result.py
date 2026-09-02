from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .engine import MatchConfig

DefaultReason = Literal["cpu", "ram", "error"]


def _entropy_01(values: tuple[int, ...]) -> float | None:
    n = len(values)
    if n == 0:
        return None
    p1 = sum(1 for v in values if v == 1) / n
    h = 0.0
    for p in (1.0 - p1, p1):
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
    x, y = values[:-1], values[1:]
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
    elapsed_time_seconds: float          # CPU seconds burned inside move()
    timed_out: bool
    memory_exceeded: bool
    errored: bool
    error_message: str | None
    memory_bytes_peak: int | None
    wall_seconds: float = 0.0            # host wall clock incl. IPC
    memory_bytes_limit: int | None = None
    rounds_played: int = 0
    default_from_round: int | None = None
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

    @property
    def healthy(self) -> bool:
        return self.default_reason is None

    @property
    def defaulted(self) -> bool:
        """True if the bot stopped playing and its remaining moves were forced to 0."""
        return self.default_reason is not None


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


def build_derived_match_data(
    *,
    submitted_moves: tuple[int, ...],
    leaderboard_moves_effective: tuple[int, ...],
) -> tuple[tuple[int, ...], tuple[int, ...], BotStats, BotStats]:
    n = min(len(submitted_moves), len(leaderboard_moves_effective))
    s = submitted_moves[:n]
    l_eff = leaderboard_moves_effective[:n]

    score = tuple(1 if a == b else 0 for a, b in zip(s, l_eff))
    leaderboard_output_deduced = tuple(
        (1 - m) if sc == 1 else m for m, sc in zip(s, score)
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