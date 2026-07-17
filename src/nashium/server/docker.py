# docker.py
from __future__ import annotations

import json
import os
import socket
import struct
import tempfile
import shutil
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from nashium.core.errors import BotLoadError, BotRuntimeError, InvalidMoveError
from nashium.core.engine import MatchConfig, MatchSummary, MatchTrace
from nashium.core.match_result import MatchResult, RuntimeStats, build_derived_match_data

try:
    import docker

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False


@dataclass
class DockerConfig:
    image: str = "nashium-runner:latest"
    memory_limit: str | int = "200m"
    cpu_quota: int = 50000
    cpu_period: int = 100000
    pids_limit: int = 64


@dataclass(frozen=True, slots=True)
class StatsSample:
    """A single stats sample for recording."""
    timestamp: float
    memory_bytes: int
    memory_peak_bytes: int
    cpu_percent: float


class BackgroundStatsMonitor:
    """
    Non-blocking stats monitor using Docker's streaming API.
    Runs in a background thread, collecting samples continuously.
    """

    def __init__(self, container, sample_interval: float = 0.5):
        self._container = container
        self._sample_interval = sample_interval
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

        self._memory_current: int = 0
        self._memory_peak: int = 0
        self._cpu_percent: float = 0.0
        self._samples: List[StatsSample] = []
        self._start_time: float = time.time()
        self._last_sample_time: float = 0.0

    def start(self) -> None:
        """Start the background monitoring thread."""
        self._running = True
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _monitor_loop(self) -> None:
        """Main monitoring loop - uses streaming API for efficiency."""
        try:
            # Use streaming API - single connection, continuous updates
            # This is MUCH faster than calling stats(stream=False) repeatedly
            for stats in self._container.stats(stream=True, decode=True):
                if not self._running:
                    break

                now = time.time()

                # Rate-limit sample storage to avoid memory bloat
                # (Docker streams stats roughly every 1 second anyway)
                should_store = (now - self._last_sample_time) >= self._sample_interval

                # Parse memory stats
                mem = stats.get("memory_stats", {})
                mem_current = mem.get("usage", 0)
                mem_peak = mem.get("max_usage") or mem_current

                # Parse CPU stats and calculate percentage
                cpu_pct = self._calculate_cpu_percent(stats)

                with self._lock:
                    self._memory_current = mem_current
                    self._memory_peak = max(self._memory_peak, mem_peak)
                    self._cpu_percent = cpu_pct

                    if should_store:
                        self._samples.append(StatsSample(
                            timestamp=now - self._start_time,
                            memory_bytes=mem_current,
                            memory_peak_bytes=self._memory_peak,
                            cpu_percent=cpu_pct,
                        ))
                        self._last_sample_time = now

        except Exception:
            # Container stopped or connection lost - this is expected
            pass

    def _calculate_cpu_percent(self, stats: dict) -> float:
        """Calculate CPU percentage from Docker stats."""
        try:
            cpu = stats.get("cpu_stats", {})
            precpu = stats.get("precpu_stats", {})

            cpu_usage = cpu.get("cpu_usage", {})
            precpu_usage = precpu.get("cpu_usage", {})

            cpu_total = cpu_usage.get("total_usage", 0)
            precpu_total = precpu_usage.get("total_usage", 0)
            cpu_delta = cpu_total - precpu_total

            sys_delta = (
                    cpu.get("system_cpu_usage", 0) -
                    precpu.get("system_cpu_usage", 0)
            )

            if sys_delta > 0 and cpu_delta > 0:
                # Get number of CPUs
                percpu = cpu_usage.get("percpu_usage")
                n_cpus = len(percpu) if percpu else 1
                return (cpu_delta / sys_delta) * n_cpus * 100.0

        except (KeyError, TypeError, ZeroDivisionError):
            pass

        return 0.0

    @property
    def memory_current(self) -> int:
        """Current memory usage in bytes."""
        with self._lock:
            return self._memory_current

    @property
    def memory_peak(self) -> int:
        """Peak memory usage in bytes."""
        with self._lock:
            return self._memory_peak

    @property
    def cpu_percent(self) -> float:
        """Current CPU usage percentage."""
        with self._lock:
            return self._cpu_percent

    def get_samples(self) -> List[StatsSample]:
        """Get a copy of all recorded samples."""
        with self._lock:
            return list(self._samples)

    def get_summary(self) -> dict:
        """Get a summary of resource usage."""
        with self._lock:
            if not self._samples:
                return {
                    "memory_peak_bytes": self._memory_peak,
                    "memory_peak_mb": self._memory_peak / (1024 * 1024),
                    "cpu_avg_percent": 0.0,
                    "cpu_max_percent": 0.0,
                    "sample_count": 0,
                }

            cpu_values = [s.cpu_percent for s in self._samples]
            return {
                "memory_peak_bytes": self._memory_peak,
                "memory_peak_mb": self._memory_peak / (1024 * 1024),
                "cpu_avg_percent": sum(cpu_values) / len(cpu_values),
                "cpu_max_percent": max(cpu_values),
                "sample_count": len(self._samples),
            }


class DockerExecutor:
    """
    Fast executor using Unix socket with binary protocol.
    Includes non-blocking background resource monitoring.
    """

    # Binary protocol constants
    CMD_MOVE = 0
    CMD_QUIT = 1
    MOVE_NONE = 255

    def __init__(
            self,
            bot_code: str,
            time_limit: float = 100.0,
            seed: int | None = None,
            config: DockerConfig | None = None,
            name: str = "Bot",
            enable_stats: bool = True,
            stats_sample_interval: float = 0.5,
    ):
        if not DOCKER_AVAILABLE:
            raise RuntimeError("docker package required")

        if not hasattr(socket, 'AF_UNIX'):
            raise RuntimeError(
                "Docker mode requires Unix sockets, which are not available on Windows.\n"
                "Please run from WSL (Windows Subsystem for Linux):\n"
                "  1. Install WSL: wsl --install\n"
                "  2. Run your project from within WSL\n"
                "  3. Docker Desktop should be configured to work with WSL"
            )

        self._config = config or DockerConfig()
        self._time_limit = time_limit
        self._name = name
        self._enable_stats = enable_stats
        self._stats_sample_interval = stats_sample_interval

        self._elapsed_time = 0.0
        self._timed_out = False
        self._errored = False
        self._error_message: str | None = None
        self._memory_bytes_peak: int | None = None
        self._memory_bytes_current: int | None = None
        self._memory_exceeded = False
        self._closed = False

        self._client = docker.from_env()
        self._container = None
        self._sock_dir: str | None = None
        self._server: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._stats_monitor: BackgroundStatsMonitor | None = None

        self._start(bot_code, seed)

    def _parse_memory_limit_bytes(self) -> int | None:
        limit = self._config.memory_limit
        if isinstance(limit, int):
            return limit
        if not isinstance(limit, str):
            return None

        s = limit.strip().lower()
        try:
            return int(s)
        except ValueError:
            pass

        units = {
            "k": 1024,
            "kb": 1024,
            "m": 1024 * 1024,
            "mb": 1024 * 1024,
            "g": 1024 * 1024 * 1024,
            "gb": 1024 * 1024 * 1024,
        }
        for suffix, mult in units.items():
            if s.endswith(suffix):
                num = s[: -len(suffix)].strip()
                try:
                    return int(float(num) * mult)
                except ValueError:
                    return None
        return None

    def poll_usage(self) -> None:
        """
        Legacy method - now a no-op since monitoring happens in background.
        Kept for API compatibility.
        """
        pass

    def _start(self, bot_code: str, seed: int | None) -> None:
        print(f"  [{self._name}] Starting container...", end="", flush=True)

        # Create Unix socket in temp directory
        self._sock_dir = tempfile.mkdtemp(prefix="nashium_")
        sock_path = os.path.join(self._sock_dir, "ipc.sock")

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(sock_path)
        self._server.listen(1)
        os.chmod(sock_path, 0o777)

        try:
            self._container = self._client.containers.run(
                image=self._config.image,
                command=["python", "/app/_docker_runner.py", "/ipc/ipc.sock"],
                detach=True,
                mem_limit=self._config.memory_limit,
                cpu_quota=self._config.cpu_quota,
                cpu_period=self._config.cpu_period,
                pids_limit=self._config.pids_limit,
                network_disabled=True,
                read_only=True,
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                volumes={self._sock_dir: {"bind": "/ipc", "mode": "rw"}},
                tmpfs={"/tmp": "size=10M,noexec,nosuid,nodev"},
                remove=False,
                user="1000:1000",
            )
        except docker.errors.ImageNotFound:
            print(" FAILED", flush=True)
            self._cleanup_socket()
            raise RuntimeError(
                f"Docker image '{self._config.image}' not found.\n"
                f"Build with: docker build -t {self._config.image} src/nashium/server/"
            )
        except Exception as e:
            print(" FAILED", flush=True)
            self._cleanup_socket()
            raise RuntimeError(f"Failed to start container: {e}")

        print(" started", flush=True)

        # Start background stats monitoring (non-blocking)
        if self._enable_stats:
            self._stats_monitor = BackgroundStatsMonitor(
                self._container,
                sample_interval=self._stats_sample_interval
            )
            self._stats_monitor.start()

        # Wait for container to connect
        print(f"  [{self._name}] Loading bot...", end="", flush=True)

        self._server.settimeout(10.0)
        try:
            self._conn, _ = self._server.accept()
            # allows docker at least 10 seconds to load properly
            self._conn.settimeout(10)
        except socket.timeout:
            print(" FAILED", flush=True)
            self._kill()
            raise RuntimeError("Container failed to connect")

        # Load bot
        try:
            self._send_load(bot_code, seed)
            print(" ready ✓", flush=True)
        except Exception as e:
            print(" FAILED", flush=True)
            self._kill()
            raise

    def _send_load(self, code: str, seed: int | None) -> None:
        """Send load command with JSON, receive JSON response."""
        msg = {"cmd": "load", "code": code}
        if seed is not None:
            msg["seed"] = seed

        data = json.dumps(msg).encode()
        self._conn.sendall(struct.pack(">I", len(data)) + data)

        # Receive response
        raw_len = self._recvall(4)
        if not raw_len:
            raise BotLoadError("Connection closed during load")

        length = struct.unpack(">I", raw_len)[0]
        resp_data = self._recvall(length)
        resp = json.loads(resp_data)

        if resp.get("status") != "ok":
            raise BotLoadError(resp.get("error", "Load failed"))

    def _recvall(self, n: int) -> bytes:
        """Receive exactly n bytes."""
        data = bytearray()
        while len(data) < n:
            chunk = self._conn.recv(n - len(data))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
        return bytes(data)

    def get_move(self, opponent_last_move: int | None) -> int:
        if self._timed_out or self._errored or self._closed or self._memory_exceeded:
            return 0

        if self._elapsed_time > self._time_limit:
            self._timed_out = True
            return 0

        opp = self.MOVE_NONE if opponent_last_move is None else opponent_last_move

        # Set socket timeout to remaining time (plus small buffer for IPC overhead)
        remaining = self._time_limit - self._elapsed_time
        self._conn.settimeout(remaining + 0.5)

        start = time.perf_counter()

        try:
            self._conn.sendall(bytes([self.CMD_MOVE, opp]))
        except Exception as e:
            self._elapsed_time += time.perf_counter() - start
            self._errored = True
            self._error_message = f"Send failed: {e}"
            self._check_oom_killed()
            return 0

        try:
            resp = self._recvall(10)
        except socket.timeout:
            self._elapsed_time += time.perf_counter() - start
            self._timed_out = True
            self._error_message = "Move timed out"
            return 0
        except Exception as e:
            self._elapsed_time += time.perf_counter() - start
            self._errored = True
            self._error_message = f"Recv failed: {e}"
            self._check_oom_killed()
            return 0

        elapsed = time.perf_counter() - start

        if len(resp) < 10:
            self._elapsed_time += elapsed
            self._errored = True
            self._error_message = "Incomplete response"
            self._check_oom_killed()
            return 0

        status, move = resp[0], resp[1]

        if status != 0:
            self._elapsed_time += elapsed
            self._errored = True
            self._error_message = f"Bot error (status={status})"
            return 0

        self._elapsed_time += elapsed

        if self._elapsed_time > self._time_limit:
            self._timed_out = True

        if move not in (0, 1):
            raise InvalidMoveError(f"Invalid move: {move}")

        return move

    def _check_oom_killed(self) -> None:
        if self._container is None:
            return

        try:
            self._container.reload()
            state = (self._container.attrs or {}).get("State") or {}
            if state.get("OOMKilled"):
                self._memory_exceeded = True
                self._errored = False
                self._error_message = "Container was OOM-killed"
        except Exception:
            return

    def _cleanup_socket(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except:
                pass
        if self._server:
            try:
                self._server.close()
            except:
                pass
        if self._sock_dir and os.path.exists(self._sock_dir):
            try:
                shutil.rmtree(self._sock_dir)
            except:
                pass

    def _kill(self) -> None:
        # Stop stats monitor first
        if self._stats_monitor:
            self._stats_monitor.stop()
            self._stats_monitor = None

        self._cleanup_socket()

        if self._container:
            try:
                self._container.kill()
            except:
                pass
            try:
                self._container.remove(force=True)
            except:
                pass

    @property
    def elapsed_time(self) -> float:
        return self._elapsed_time

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    @property
    def memory_bytes_peak(self) -> int | None:
        if self._stats_monitor:
            peak = self._stats_monitor.memory_peak
            if peak > 0:
                return peak
        return self._memory_bytes_peak

    @property
    def memory_bytes_current(self) -> int | None:
        if self._stats_monitor:
            current = self._stats_monitor.memory_current
            if current > 0:
                return current
        return self._memory_bytes_current

    @property
    def cpu_percent(self) -> float:
        """Current CPU usage percentage."""
        if self._stats_monitor:
            return self._stats_monitor.cpu_percent
        return 0.0

    @property
    def memory_exceeded(self) -> bool:
        return self._memory_exceeded

    @property
    def errored(self) -> bool:
        return self._errored

    @property
    def error_message(self) -> str | None:
        return self._error_message

    def get_stats_samples(self) -> List[StatsSample]:
        """
        Get all recorded stats samples.
        Useful for saving to file after the match.
        """
        if self._stats_monitor:
            return self._stats_monitor.get_samples()
        return []

    def get_stats_summary(self) -> dict:
        """
        Get a summary of resource usage.
        Returns dict with memory_peak_bytes, memory_peak_mb, cpu_avg_percent, cpu_max_percent.
        """
        if self._stats_monitor:
            return self._stats_monitor.get_summary()
        return {
            "memory_peak_bytes": self._memory_bytes_peak or 0,
            "memory_peak_mb": (self._memory_bytes_peak or 0) / (1024 * 1024),
            "cpu_avg_percent": 0.0,
            "cpu_max_percent": 0.0,
            "sample_count": 0,
        }

    def save_stats_csv(self, filepath: str) -> None:
        """
        Save stats samples to a CSV file.

        Args:
            filepath: Path to the output CSV file.
        """
        samples = self.get_stats_samples()
        with open(filepath, 'w') as f:
            f.write("timestamp,memory_bytes,memory_peak_bytes,cpu_percent\n")
            for s in samples:
                f.write(f"{s.timestamp:.3f},{s.memory_bytes},{s.memory_peak_bytes},{s.cpu_percent:.2f}\n")

    def save_stats_json(self, filepath: str) -> None:
        """
        Save stats samples and summary to a JSON file.

        Args:
            filepath: Path to the output JSON file.
        """
        samples = self.get_stats_samples()
        data = {
            "summary": self.get_stats_summary(),
            "samples": [
                {
                    "timestamp": s.timestamp,
                    "memory_bytes": s.memory_bytes,
                    "memory_peak_bytes": s.memory_peak_bytes,
                    "cpu_percent": s.cpu_percent,
                }
                for s in samples
            ]
        }
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._conn:
            try:
                self._conn.sendall(bytes([self.CMD_QUIT, 0]))
            except:
                pass

        self._kill()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Backend(ABC):
    """Abstract backend for bot execution."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name."""
        ...

    @abstractmethod
    def run_match_result_between_files(
        self,
        path_a: Path,
        path_b: Path,
        seed: int,
        config: MatchConfig,
        capture_history: bool = False,
    ) -> MatchResult:
        ...


class DockerBackend(Backend):
    """Docker container isolation (slow, full isolation)."""

    def __init__(self):
        super().__init__()
        self._validate_docker()

    def _validate_docker(self) -> None:
        """Validate Docker is available and properly configured."""
        if not DOCKER_AVAILABLE:
            raise RuntimeError(
                "Docker backend requires 'docker' package.\n"
                "Install with: pip install docker"
            )

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

    def run_match_result_between_files(
            self,
            path_a: Path,
            path_b: Path,
            seed: int,
            config: MatchConfig,
            capture_history: bool = False,
    ) -> MatchResult:
        """Satisfy the Backend interface by reading files and executing from strings."""
        with open(path_a, "r", encoding="utf-8") as f_a:
            submitted_source = f_a.read()
        with open(path_b, "r", encoding="utf-8") as f_b:
            opponent_source = f_b.read()

        return self.run_match_result_from_strings(
            submitted_source,
            opponent_source,
            seed,
            config,
            capture_history
        )

    def run_match_result_from_strings(self, submitted_source: str, opponent_source: str, seed: int, config: MatchConfig,
                                      capture_history: bool = False) -> MatchResult:
        start = time.perf_counter()

        with DockerExecutor(
            submitted_source,
            time_limit=config.max_total_time_seconds_per_bot,
            seed=seed,
            config=DockerConfig(memory_limit=(config.max_total_memory_bytes_per_bot or "200m")),
        ) as submitted:
            with DockerExecutor(
                opponent_source,
                time_limit=config.max_total_time_seconds_per_bot,
                seed=seed,
                config=DockerConfig(memory_limit=(config.max_total_memory_bytes_per_bot or "200m")),
            ) as opponent:
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
                    submitted=submitted_stats,
                    leaderboard=opponent_stats,
                    wall_time_seconds=wall_time,
                    submitted_moves=submitted_moves_t,
                    leaderboard_moves_raw=opponent_raw_t,
                    leaderboard_moves_effective=opponent_eff_t,
                    submitted_performance=score_per_round,
                    leaderboard_output_deduced=leaderboard_output_deduced,
                    submitted_stats=s_bot_stats,
                    leaderboard_stats=o_bot_stats,
                )

                return match_result