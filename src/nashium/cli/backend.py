"""
Backend abstraction for bot execution.

LocalBackend: Fast in-process execution (current behavior)
SandboxBackend: Safe subprocess execution for untrusted code
DockerBackend: Full container isolation (matches server environment)
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

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name."""
        ...

    @abstractmethod
    def run_match_file_vs_source(
            self,
            submitted_path: Path,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
    ) -> MatchSummary:
        ...

    @abstractmethod
    def run_match_trace_file_vs_source(
            self,
            submitted_path: Path,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
    ) -> MatchTrace:
        ...

    @abstractmethod
    def run_match_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
    ) -> MatchSummary:
        ...

    @abstractmethod
    def run_match_trace_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
    ) -> MatchTrace:
        ...


def _load_bot_from_source(source: str, seed: int):
    """Load a bot instance from source code string."""
    namespace = {
        "__name__": "__bot__",
        "RoundState": RoundState,
    }
    exec(compile(source, "<bot>", "exec"), namespace)

    bot_class = namespace.get("Bot")

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

    try:
        return bot_class(seed=seed)
    except TypeError:
        try:
            return bot_class(seed)
        except TypeError:
            return bot_class()


class LocalBackend(Backend):
    """Fast in-process execution for trusted code."""

    @property
    def name(self) -> str:
        return "local"

    def run_match_file_vs_source(
            self,
            submitted_path: Path,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
    ) -> MatchSummary:
        from .loader import load_bot_from_file

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
        from .loader import load_bot_from_file

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
        from .loader import load_bot_from_file

        bot_a = load_bot_from_file(path_a, seed)
        bot_b = load_bot_from_file(path_b, seed)
        return run_match(bot_a, bot_b, config)

    def run_match_trace_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
    ) -> MatchTrace:
        from .loader import load_bot_from_file

        bot_a = load_bot_from_file(path_a, seed)
        bot_b = load_bot_from_file(path_b, seed)
        return run_match_trace(bot_a, bot_b, config)


class _IsolatedBackend(Backend):
    """
    Base class for backends that run bots in isolated processes.

    Shared game loop for SandboxBackend and DockerBackend.
    """

    def __init__(self):
        pass

    @abstractmethod
    def _create_executor(self, source: str, seed: int, config: MatchConfig):
        """Create an executor for the given source code."""
        ...

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
                rounds_played = 0

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
                    rounds_played += 1

                wall_time = time.perf_counter() - start

                # Check for errors (distinct from timeouts)
                submitted_errored = getattr(submitted, 'errored', False)
                opponent_errored = getattr(opponent, 'errored', False)

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

                # Log errors for debugging (optional)
                if submitted_errored:
                    error_msg = getattr(submitted, 'error_message', 'Unknown')
                    print(f"DEBUG: Submitted bot errored: {error_msg}")
                if opponent_errored:
                    error_msg = getattr(opponent, 'error_message', 'Unknown')
                    print(f"DEBUG: Opponent bot errored: {error_msg}")

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

    def run_match_trace_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
    ) -> MatchTrace:
        source_a = path_a.read_text()
        source_b = path_b.read_text()
        return self._run_match_internal(
            source_a, source_b, seed, config, capture_history=True
        )


class SandboxBackend(_IsolatedBackend):
    """Subprocess isolation (fast, partial isolation)."""

    @property
    def name(self) -> str:
        return "subprocess sandbox"

    def _create_executor(self, source: str, seed: int, config: MatchConfig):
        from ..server.sandbox import SubprocessExecutor

        return SubprocessExecutor(
            source,
            time_limit=config.max_total_time_seconds_per_bot,
            seed=seed,
        )


class DockerBackend(_IsolatedBackend):
    """Docker container isolation (slow, full isolation)."""

    def __init__(self):
        super().__init__()
        self._validate_docker()

    def _validate_docker(self) -> None:
        """Validate Docker is available and properly configured."""
        from ..server.docker import DOCKER_AVAILABLE, DockerConfig

        if not DOCKER_AVAILABLE:
            raise RuntimeError(
                "Docker backend requires 'docker' package.\n"
                "Install with: pip install docker"
            )

        import docker

        try:
            client = docker.from_env()
            client.ping()
        except Exception as e:
            raise RuntimeError(
                f"Cannot connect to Docker daemon. Is Docker running?\n"
                f"Error: {e}"
            )

        config = DockerConfig()
        try:
            client.images.get(config.image)
        except docker.errors.ImageNotFound:
            raise RuntimeError(
                f"Docker image '{config.image}' not found.\n"
                f"Build with: docker build -t {config.image} src/nashium/server/"
            )

    @property
    def name(self) -> str:
        return "docker container"

    def _create_executor(self, source: str, seed: int, config: MatchConfig):
        from ..server.docker import DockerExecutor

        return DockerExecutor(
            source,
            time_limit=config.max_total_time_seconds_per_bot,
            seed=seed,
        )


def get_backend(
    sandbox: bool = False,
    docker: bool = False,
) -> Backend:
    """Get the appropriate backend."""

    # Args:
    #     sandbox: Use subprocess isolation
    #     docker: Use Docker container isolation (overrides sandbox)

    if docker:
        return DockerBackend()
    elif sandbox:
        return SandboxBackend()
    return LocalBackend()