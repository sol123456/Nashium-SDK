"""
Backend abstraction for bot execution.

LocalBackend: Fast in-process execution (current behavior)
SandboxBackend: Safe subprocess execution for untrusted code
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..core import (
    InteractionResult,
    MatchConfig,
    MatchSummary,
    MatchTrace,
    run_match,
    run_match_trace,
    RoundState,
)


class Backend(ABC):
    """Abstract backend for bot execution."""

    @abstractmethod
    def run_match_file_vs_source(
            self,
            submitted_path: Path,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
    ) -> MatchSummary:
        """Run match: user file vs opponent source code."""
        ...

    @abstractmethod
    def run_match_trace_file_vs_source(
            self,
            submitted_path: Path,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
    ) -> MatchTrace:
        """Run match with trace: user file vs opponent source code."""
        ...

    @abstractmethod
    def run_match_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
    ) -> MatchSummary:
        """Run match between two bot files."""
        ...


def _load_bot_from_source(source: str, seed: int):
    """Load a bot instance from source code string."""
    namespace = {
        "__name__": "__bot__",
        "RoundState": RoundState,  # ← Add this import!
    }
    exec(compile(source, "<bot>", "exec"), namespace)

    # Look for Bot class first (user bots)
    bot_class = namespace.get("Bot")

    # If not found, look for any class with a move method (sample bots)
    if bot_class is None:
        for name, obj in namespace.items():
            if (
                    isinstance(obj, type)
                    and callable(getattr(obj, "move", None))
                    and name not in ("RoundState",)
            ):
                bot_class = obj
                break

    if bot_class is None:
        raise ValueError("No bot class found in source code")

    # Try instantiation with seed (now that sample bots accept it!)
    try:
        return bot_class(seed=seed)  # ← Try keyword first (most explicit)
    except TypeError:
        try:
            return bot_class(seed)  # ← Then positional
        except TypeError:
            return bot_class()  # ← Finally no args (shouldn't happen now)


class LocalBackend(Backend):
    """
    Fast in-process execution for trusted code.

    This is the default - runs bots directly in the current process.
    """

    def run_match_file_vs_source(
            self,
            submitted_path: Path,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
    ) -> MatchSummary:
        from .loading import load_bot_from_file

        submitted = load_bot_from_file(submitted_path, seed)
        opponent = _load_bot_from_source(opponent_source, seed)
        return run_match(submitted, opponent, config)

    def run_match_trace_file_vs_source(
            self,
            submitted_path: Path,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
    ) -> MatchTrace:
        from .loading import load_bot_from_file

        submitted = load_bot_from_file(submitted_path, seed)
        opponent = _load_bot_from_source(opponent_source, seed)
        return run_match_trace(submitted, opponent, config)

    def run_match_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
    ) -> MatchSummary:
        from .loading import load_bot_from_file

        bot_a = load_bot_from_file(path_a, seed)
        bot_b = load_bot_from_file(path_b, seed)
        return run_match(bot_a, bot_b, config)


class SandboxBackend(Backend):
    """
    Safe subprocess execution for untrusted code.

    Runs each bot in a separate subprocess with timeouts.
    Slower than LocalBackend but provides isolation.
    """

    def __init__(self, move_timeout: float = 5.0):
        self._move_timeout = move_timeout

    def _create_executor(self, source: str, seed: int, config: MatchConfig):
        from ..server.sandbox import SubprocessExecutor

        return SubprocessExecutor(
            source,
            time_limit=config.max_total_time_seconds_per_bot,
            move_timeout=self._move_timeout,
            seed=seed,
        )

    def _run_match_internal(
            self,
            submitted_source: str,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
            capture_history: bool,
    ) -> MatchSummary | MatchTrace:
        start = time.perf_counter()

        with self._create_executor(submitted_source, seed, config) as submitted:
            with self._create_executor(opponent_source, seed, config) as opponent:
                submitted_wins = 0
                last_submitted_move: int | None = None
                last_opponent_effective: int | None = None

                if capture_history:
                    submitted_history: list[int] = []
                    opponent_raw_history: list[int] = []
                    opponent_effective_history: list[int] = []

                for _ in range(config.rounds):
                    s_move = submitted.get_move(last_opponent_effective)
                    o_move_raw = opponent.get_move(last_submitted_move)
                    o_move = 1 - o_move_raw

                    if s_move == o_move:
                        submitted_wins += 1

                    if capture_history:
                        submitted_history.append(s_move)
                        opponent_raw_history.append(o_move_raw)
                        opponent_effective_history.append(o_move)

                    last_submitted_move = s_move
                    last_opponent_effective = o_move

                wall_time = time.perf_counter() - start

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

                summary = MatchSummary(
                    rounds=config.rounds,
                    submitted_wins=submitted_wins,
                    submitted_win_rate=submitted_wins / config.rounds if config.rounds else 0.0,
                    result=result,
                    stat_sig=stat_sig,
                    submitted_time_seconds=submitted.elapsed_time,
                    leaderboard_time_seconds=opponent.elapsed_time,
                    wall_time_seconds=wall_time,
                    submitted_timed_out=submitted.timed_out,
                    leaderboard_timed_out=opponent.timed_out,
                )

                if capture_history:
                    return MatchTrace(
                        summary=summary,
                        submitted_moves=tuple(submitted_history),
                        leaderboard_moves_raw=tuple(opponent_raw_history),
                        leaderboard_moves_effective=tuple(opponent_effective_history),
                    )
                return summary

    def run_match_file_vs_source(
            self,
            submitted_path: Path,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
    ) -> MatchSummary:
        submitted_source = submitted_path.read_text()
        return self._run_match_internal(
            submitted_source, opponent_source, seed, config, capture_history=False
        )

    def run_match_trace_file_vs_source(
            self,
            submitted_path: Path,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
    ) -> MatchTrace:
        submitted_source = submitted_path.read_text()
        return self._run_match_internal(
            submitted_source, opponent_source, seed, config, capture_history=True
        )

    def run_match_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
    ) -> MatchSummary:
        source_a = path_a.read_text()
        source_b = path_b.read_text()
        return self._run_match_internal(
            source_a, source_b, seed, config, capture_history=False
        )


def get_backend(sandbox: bool = False, move_timeout: float = 5.0) -> Backend:
    """Get the appropriate backend."""
    if sandbox:
        return SandboxBackend(move_timeout=move_timeout)
    return LocalBackend()