from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from .errors import BotTimeoutError, InvalidMoveError


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
    invert_opponent: bool = True
    stat_sig_win_threshold: int = 5155
    max_total_time_seconds_per_bot: float = 100.0


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


@dataclass(frozen=True)
class MatchTrace:
    summary: MatchSummary
    submitted_moves: tuple[int, ...]
    leaderboard_moves_raw: tuple[int, ...]
    leaderboard_moves_effective: tuple[int, ...]


def _validate_move(move: int, round_index: int, bot_name: str = "Bot") -> int:
    if move in (0, 1):
        return move
    raise InvalidMoveError(
        f"{bot_name} returned invalid move {move!r} on round {round_index}. Expected 0 or 1."
    )


def _play_rounds(
    submitted_bot,
    leaderboard_bot,
    config: MatchConfig,
    *,
    capture_histories: bool,
):
    submitted_history: list[int] = []
    leaderboard_raw_history: list[int] = []
    leaderboard_effective_history: list[int] = []

    submitted_wins = 0
    submitted_time = 0.0
    leaderboard_time = 0.0

    for i in range(config.rounds):
        s_state = RoundState(i, tuple(submitted_history), tuple(leaderboard_effective_history))
        l_state = RoundState(i, tuple(leaderboard_raw_history), tuple(submitted_history))

        s_start = time.perf_counter()
        s_move = _validate_move(int(submitted_bot.move(s_state)), i, "Submitted bot")
        submitted_time += time.perf_counter() - s_start
        if submitted_time > config.max_total_time_seconds_per_bot:
            raise BotTimeoutError(
                f"Submitted bot exceeded total time budget of {config.max_total_time_seconds_per_bot} seconds"
            )

        l_start = time.perf_counter()
        l_move_raw = _validate_move(int(leaderboard_bot.move(l_state)), i, "Leaderboard bot")
        leaderboard_time += time.perf_counter() - l_start
        if leaderboard_time > config.max_total_time_seconds_per_bot:
            raise BotTimeoutError(
                f"Leaderboard bot exceeded total time budget of {config.max_total_time_seconds_per_bot} seconds"
            )

        l_move = (1 - l_move_raw) if config.invert_opponent else l_move_raw

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
            submitted_time,
            leaderboard_time,
        )

    return (
        submitted_wins,
        None,
        None,
        None,
        submitted_time,
        leaderboard_time,
    )


def run_match(submitted_bot, leaderboard_bot, config: MatchConfig) -> MatchSummary:
    start = time.perf_counter()

    submitted_wins, _s_hist, _l_raw, _l_eff, submitted_time, leaderboard_time = _play_rounds(
        submitted_bot,
        leaderboard_bot,
        config,
        capture_histories=False,
    )

    end = time.perf_counter()

    win_rate = submitted_wins / config.rounds if config.rounds else 0.0
    stat_sig = (
        submitted_wins >= config.stat_sig_win_threshold
        or submitted_wins <= (config.rounds - config.stat_sig_win_threshold)
    )

    if submitted_wins >= config.stat_sig_win_threshold:
        result = InteractionResult.S_WIN
    elif submitted_wins <= (config.rounds - config.stat_sig_win_threshold):
        result = InteractionResult.S_LOSS
    else:
        if submitted_wins == config.rounds // 2:
            result = InteractionResult.DRAW
        elif submitted_wins > config.rounds // 2:
            result = InteractionResult.STAT_DRAW_S_WIN
        else:
            result = InteractionResult.STAT_DRAW_S_LOSS

    return MatchSummary(
        rounds=config.rounds,
        submitted_wins=submitted_wins,
        submitted_win_rate=win_rate,
        result=result,
        stat_sig=stat_sig,
        submitted_time_seconds=submitted_time,
        leaderboard_time_seconds=leaderboard_time,
        wall_time_seconds=end - start,
    )


def run_match_trace(submitted_bot, leaderboard_bot, config: MatchConfig) -> MatchTrace:
    start = time.perf_counter()

    submitted_wins, s_hist, l_raw, l_eff, submitted_time, leaderboard_time = _play_rounds(
        submitted_bot,
        leaderboard_bot,
        config,
        capture_histories=True,
    )

    end = time.perf_counter()

    win_rate = submitted_wins / config.rounds if config.rounds else 0.0
    stat_sig = (
        submitted_wins >= config.stat_sig_win_threshold
        or submitted_wins <= (config.rounds - config.stat_sig_win_threshold)
    )

    if submitted_wins >= config.stat_sig_win_threshold:
        result = InteractionResult.S_WIN
    elif submitted_wins <= (config.rounds - config.stat_sig_win_threshold):
        result = InteractionResult.S_LOSS
    else:
        if submitted_wins == config.rounds // 2:
            result = InteractionResult.DRAW
        elif submitted_wins > config.rounds // 2:
            result = InteractionResult.STAT_DRAW_S_WIN
        else:
            result = InteractionResult.STAT_DRAW_S_LOSS

    summary = MatchSummary(
        rounds=config.rounds,
        submitted_wins=submitted_wins,
        submitted_win_rate=win_rate,
        result=result,
        stat_sig=stat_sig,
        submitted_time_seconds=submitted_time,
        leaderboard_time_seconds=leaderboard_time,
        wall_time_seconds=end - start,
    )

    return MatchTrace(
        summary=summary,
        submitted_moves=s_hist or (),
        leaderboard_moves_raw=l_raw or (),
        leaderboard_moves_effective=l_eff or (),
    )