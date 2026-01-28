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
from ..core.match_result import MatchResult, RuntimeStats, build_derived_match_data


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

    def run_match_result_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
            capture_history: bool = False,
    ) -> MatchResult:
        if capture_history:
            trace = self.run_match_trace_between_files(path_a, path_b, seed, config)
            summary = trace.summary
            submitted_moves = trace.submitted_moves
            leaderboard_moves_raw = trace.leaderboard_moves_raw
            leaderboard_moves_effective = trace.leaderboard_moves_effective
            submitted_cpu_usage_samples = trace.submitted_cpu_usage_samples
            submitted_ram_usage_samples = trace.submitted_ram_usage_samples
            leaderboard_cpu_usage_samples = trace.leaderboard_cpu_usage_samples
            leaderboard_ram_usage_samples = trace.leaderboard_ram_usage_samples
        else:
            summary = self.run_match_between_files(path_a, path_b, seed, config)
            submitted_moves = ()
            leaderboard_moves_raw = ()
            leaderboard_moves_effective = ()
            submitted_cpu_usage_samples = ()
            submitted_ram_usage_samples = ()
            leaderboard_cpu_usage_samples = ()
            leaderboard_ram_usage_samples = ()

        score_per_round: tuple[int, ...] = ()
        leaderboard_output_deduced: tuple[int, ...] = ()
        s_bot_stats = None
        o_bot_stats = None
        if submitted_moves and leaderboard_moves_effective:
            score_per_round, leaderboard_output_deduced, s_bot_stats, o_bot_stats = build_derived_match_data(
                submitted_moves=submitted_moves,
                leaderboard_moves_effective=leaderboard_moves_effective,
            )

        submitted_stats = RuntimeStats(
            elapsed_time_seconds=summary.submitted_time_seconds,
            timed_out=summary.submitted_timed_out,
            memory_exceeded=summary.submitted_memory_exceeded,
            errored=False,
            error_message=None,
            memory_bytes_peak=summary.submitted_memory_bytes_peak,
            cpu_usage_samples=submitted_cpu_usage_samples,
            ram_usage_samples=submitted_ram_usage_samples,
        )

        leaderboard_stats = RuntimeStats(
            elapsed_time_seconds=summary.leaderboard_time_seconds,
            timed_out=summary.leaderboard_timed_out,
            memory_exceeded=summary.leaderboard_memory_exceeded,
            errored=False,
            error_message=None,
            memory_bytes_peak=summary.leaderboard_memory_bytes_peak,
            cpu_usage_samples=leaderboard_cpu_usage_samples,
            ram_usage_samples=leaderboard_ram_usage_samples,
        )

        return MatchResult(
            seed=seed,
            config=config,
            rounds=summary.rounds,
            submitted_wins=summary.submitted_wins,
            submitted_win_rate=summary.submitted_win_rate,
            result=summary.result,
            stat_sig=summary.stat_sig,
            submitted=submitted_stats,
            leaderboard=leaderboard_stats,
            wall_time_seconds=summary.wall_time_seconds,
            submitted_moves=submitted_moves,
            leaderboard_moves_raw=leaderboard_moves_raw,
            leaderboard_moves_effective=leaderboard_moves_effective,
            score_per_round=score_per_round,
            leaderboard_output_deduced=leaderboard_output_deduced,
            submitted_stats=s_bot_stats,
            leaderboard_stats=o_bot_stats,
        )


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
        match_result = self._run_match_result_internal(
            submitted_source=submitted_source,
            opponent_source=opponent_source,
            seed=seed,
            config=config,
            capture_history=capture_history,
        )

        if capture_history:
            return match_result.to_match_trace()
        return match_result.to_match_summary()

    def _run_match_result_internal(
            self,
            submitted_source: str,
            opponent_source: str,
            seed: int,
            config: MatchConfig,
            capture_history: bool,
    ) -> MatchResult:
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
                    submitted_cpu_usage_samples: list[int] = []
                    submitted_ram_usage_samples: list[int] = []
                    opponent_cpu_usage_samples: list[int] = []
                    opponent_ram_usage_samples: list[int] = []

                    sample_interval = max(1, int(config.rounds * 0.005))

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

                    if capture_history and (
                            rounds_played % sample_interval == 0
                            or rounds_played == config.rounds
                    ):
                        for ex in (submitted, opponent):
                            poll = getattr(ex, "poll_usage", None)
                            if callable(poll):
                                poll()

                        time_budget = config.max_total_time_seconds_per_bot
                        mem_budget = config.max_total_memory_bytes_per_bot

                        sub_cpu_frac = (submitted.elapsed_time / time_budget) if time_budget else 0.0
                        sub_cpu_scaled = int(max(0.0, min(1.0, sub_cpu_frac)) * 1000)
                        submitted_cpu_usage_samples.append(sub_cpu_scaled)

                        opp_cpu_frac = (opponent.elapsed_time / time_budget) if time_budget else 0.0
                        opp_cpu_scaled = int(max(0.0, min(1.0, opp_cpu_frac)) * 1000)
                        opponent_cpu_usage_samples.append(opp_cpu_scaled)

                        sub_mem_current = getattr(submitted, "memory_bytes_current", None)
                        if sub_mem_current is None:
                            sub_mem_current = getattr(submitted, "memory_bytes_peak", None)

                        if mem_budget and sub_mem_current is not None:
                            sub_mem_frac = sub_mem_current / mem_budget
                            sub_mem_scaled = int(max(0.0, min(1.0, sub_mem_frac)) * 1000)
                        else:
                            sub_mem_scaled = 0
                        submitted_ram_usage_samples.append(sub_mem_scaled)

                        opp_mem_current = getattr(opponent, "memory_bytes_current", None)
                        if opp_mem_current is None:
                            opp_mem_current = getattr(opponent, "memory_bytes_peak", None)

                        if mem_budget and opp_mem_current is not None:
                            opp_mem_frac = opp_mem_current / mem_budget
                            opp_mem_scaled = int(max(0.0, min(1.0, opp_mem_frac)) * 1000)
                        else:
                            opp_mem_scaled = 0
                        opponent_ram_usage_samples.append(opp_mem_scaled)

                wall_time = time.perf_counter() - start

                for ex in (submitted, opponent):
                    poll = getattr(ex, "poll_usage", None)
                    if callable(poll):
                        poll()

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

                submitted_stats = RuntimeStats(
                    elapsed_time_seconds=submitted.elapsed_time,
                    timed_out=submitted.timed_out,
                    memory_exceeded=getattr(submitted, "memory_exceeded", False),
                    errored=bool(submitted_errored),
                    error_message=getattr(submitted, "error_message", None),
                    memory_bytes_peak=getattr(submitted, "memory_bytes_peak", None),
                    cpu_usage_samples=tuple(submitted_cpu_usage_samples) if capture_history else (),
                    ram_usage_samples=tuple(submitted_ram_usage_samples) if capture_history else (),
                )

                opponent_stats = RuntimeStats(
                    elapsed_time_seconds=opponent.elapsed_time,
                    timed_out=opponent.timed_out,
                    memory_exceeded=getattr(opponent, "memory_exceeded", False),
                    errored=bool(opponent_errored),
                    error_message=getattr(opponent, "error_message", None),
                    memory_bytes_peak=getattr(opponent, "memory_bytes_peak", None),
                    cpu_usage_samples=tuple(opponent_cpu_usage_samples) if capture_history else (),
                    ram_usage_samples=tuple(opponent_ram_usage_samples) if capture_history else (),
                )

                submitted_moves_t = tuple(submitted_history) if capture_history else ()
                opponent_raw_t = tuple(opponent_raw_history) if capture_history else ()
                opponent_eff_t = tuple(opponent_effective_history) if capture_history else ()

                score_per_round: tuple[int, ...] = ()
                leaderboard_output_deduced: tuple[int, ...] = ()
                s_bot_stats = None
                o_bot_stats = None
                if capture_history:
                    score_per_round, leaderboard_output_deduced, s_bot_stats, o_bot_stats = build_derived_match_data(
                        submitted_moves=submitted_moves_t,
                        leaderboard_moves_effective=opponent_eff_t,
                    )

                match_result = MatchResult(
                    seed=seed,
                    config=config,
                    rounds=config.rounds,
                    submitted_wins=submitted_wins,
                    submitted_win_rate=submitted_wins / config.rounds if config.rounds else 0.0,
                    result=result,
                    stat_sig=stat_sig,
                    submitted=submitted_stats,
                    leaderboard=opponent_stats,
                    wall_time_seconds=wall_time,
                    submitted_moves=submitted_moves_t,
                    leaderboard_moves_raw=opponent_raw_t,
                    leaderboard_moves_effective=opponent_eff_t,
                    score_per_round=score_per_round,
                    leaderboard_output_deduced=leaderboard_output_deduced,
                    submitted_stats=s_bot_stats,
                    leaderboard_stats=o_bot_stats,
                )

                return match_result

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
            memory_limit_bytes=config.max_total_memory_bytes_per_bot,
        )

    def run_match_result_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
            capture_history: bool = False,
    ) -> MatchResult:
        source_a = path_a.read_text()
        source_b = path_b.read_text()
        return self._run_match_result_internal(
            source_a, source_b, seed, config, capture_history=capture_history
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
        from ..server.docker import DockerConfig, DockerExecutor

        return DockerExecutor(
            source,
            time_limit=config.max_total_time_seconds_per_bot,
            seed=seed,
            config=DockerConfig(memory_limit=(config.max_total_memory_bytes_per_bot or "200m")),
        )

    def run_match_result_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
            capture_history: bool = False,
    ) -> MatchResult:
        source_a = path_a.read_text()
        source_b = path_b.read_text()
        return self._run_match_result_internal(
            source_a, source_b, seed, config, capture_history=capture_history
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