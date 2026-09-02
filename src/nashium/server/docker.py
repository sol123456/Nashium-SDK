"""Docker-sandboxed match execution. The only supported execution mode.

Fault model
-----------
Bot faults  (load failure, crash, bad move, CPU/wall timeout, OOM) are RECORDED.
            The executor defaults to move 0 for every remaining round and the
            match completes normally. Never raises.

Harness faults (no daemon, missing/stale image, container won't start) RAISE
            MatchExecutionError. The caller must treat these as blocking: submit
            nothing and retry the same match later.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import struct
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass

import docker
from docker.errors import ImageNotFound

from nashium.core.engine import MatchConfig
from nashium.core.errors import MatchExecutionError
from nashium.core.match_result import MatchResult, RuntimeStats, build_derived_match_data

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 2

LABEL_ROLE = "com.nashium.role"
LABEL_ROLE_VALUE = "bot-runner"
LABEL_INSTANCE = "com.nashium.worker-instance"

# Unique per worker process, so concurrent workers on one host never reap each
# other's live containers (R2).
INSTANCE_ID = uuid.uuid4().hex

SOCK_DIR_PREFIX = "nashium_"
MAX_LOAD_RESPONSE = 1024 * 1024

_client = None
_client_lock = threading.Lock()


def get_client():
    """Process-wide shared Docker client. Creating one per match leaks FDs."""
    global _client
    with _client_lock:
        if _client is None:
            try:
                c = docker.from_env()
                c.ping()
            except Exception as e:  # noqa: BLE001
                raise MatchExecutionError(
                    f"Cannot connect to the Docker daemon (is it running?): {e}"
                ) from e
            _client = c
        return _client


def reset_client() -> None:
    """Drop the cached client. Call after a harness failure: if the daemon has
    restarted, the pooled connections are dead and every retry would fail."""
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
        _client = None


@dataclass(frozen=True)
class DockerConfig:
    image: str = "nashium-runner:latest"
    memory_limit: int | str = 200 * 1024 * 1024
    cpu_quota: int = 100_000    # 1 full core: CPU-seconds ~= wall-seconds
    cpu_period: int = 100_000
    pids_limit: int = 64
    tmpfs_size: str = "64m"
    connect_timeout: float = 30.0
    load_timeout: float = 60.0
    stats_enabled: bool = True
    log_max_size: str = "1m"


def parse_memory_limit_bytes(limit: int | str | None) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, int):
        return limit
    s = str(limit).strip().lower()
    try:
        return int(s)
    except ValueError:
        pass
    units = {"kb": 1024, "mb": 1024**2, "gb": 1024**3,
             "k": 1024, "m": 1024**2, "g": 1024**3}
    for suffix in sorted(units, key=len, reverse=True):
        if s.endswith(suffix):
            try:
                return int(float(s[: -len(suffix)].strip()) * units[suffix])
            except ValueError:
                return None
    return None


# ----------------------------------------------------------------- lifecycle

def cleanup_stale_containers(max_age_seconds: float = 900.0) -> int:
    """Reap bot containers abandoned by a crashed worker.

    Only touches containers older than max_age_seconds (a match cannot exceed
    ~11 minutes) OR belonging to this worker instance. This keeps concurrent
    workers on the same host from killing each other's live matches.
    """
    try:
        client = get_client()
        containers = client.containers.list(
            all=True, filters={"label": f"{LABEL_ROLE}={LABEL_ROLE_VALUE}"}
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not list bot containers: %s", e)
        return 0

    now = time.time()
    removed = 0
    for c in containers:
        try:
            labels = (c.attrs.get("Config") or {}).get("Labels") or {}
            mine = labels.get(LABEL_INSTANCE) == INSTANCE_ID
            created = c.attrs.get("Created", "")
            age = float("inf")
            if created:
                try:
                    from datetime import datetime, timezone
                    ts = created.split(".")[0].rstrip("Z")
                    dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
                    age = now - dt.timestamp()
                except (ValueError, TypeError):
                    pass
            if mine or age > max_age_seconds:
                c.remove(force=True)
                removed += 1
        except Exception:  # noqa: BLE001
            pass

    if removed:
        logger.info("Removed %d stale bot container(s)", removed)
    return removed


def cleanup_stale_socket_dirs(max_age_seconds: float = 3600.0) -> int:
    root = tempfile.gettempdir()
    now = time.time()
    removed = 0
    try:
        entries = os.listdir(root)
    except OSError:
        return 0
    for name in entries:
        if not name.startswith(SOCK_DIR_PREFIX):
            continue
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path) and (now - os.path.getmtime(path)) > max_age_seconds:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed


def verify_environment(config: DockerConfig | None = None) -> None:
    """Startup preflight. Raises MatchExecutionError with an actionable message.

    Includes a live protocol handshake, so a stale image is caught here rather
    than silently timing out every bot in every subsequent match (R1).
    """
    config = config or DockerConfig()

    if not hasattr(socket, "AF_UNIX"):
        raise MatchExecutionError(
            "Unix sockets are unavailable. Run the worker from Linux or WSL2."
        )

    client = get_client()
    try:
        client.images.get(config.image)
    except ImageNotFound as e:
        raise MatchExecutionError(
            f"Docker image '{config.image}' not found.\n"
            f"Build it with: docker build -t {config.image} src/nashium/server/"
        ) from e

    probe = "class Bot:\n    def move(self, state):\n        return 0\n"
    ex = DockerExecutor(probe, name="preflight", seed=0, config=config)
    try:
        if ex.faulted:
            raise MatchExecutionError(
                f"Preflight bot failed to run: {ex.error_message}"
            )
        if ex.get_move(None) != 0 or ex.faulted:
            raise MatchExecutionError(
                f"Preflight bot returned an unexpected result: {ex.error_message}"
            )
    finally:
        ex.close()
    logger.info("Docker preflight OK (image=%s, protocol=v%d)",
                config.image, PROTOCOL_VERSION)


# ------------------------------------------------------------ stats monitor

class _StatsMonitor:
    """Background resource sampler over Docker's streaming stats API."""

    def __init__(self, container):
        self._container = container
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stream = None
        self._mem_current = 0
        self._mem_peak = 0
        self._cpu_percent = 0.0

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        try:
            self._stream = self._container.stats(stream=True, decode=True)
            for stats in self._stream:
                if not self._running:
                    break
                mem = stats.get("memory_stats") or {}
                current = mem.get("usage", 0) or 0
                peak = mem.get("max_usage") or current  # cgroup v2 has no max_usage
                pct = self._cpu_percent_of(stats)
                with self._lock:
                    self._mem_current = current
                    self._mem_peak = max(self._mem_peak, peak, current)
                    self._cpu_percent = pct
        except Exception:  # noqa: BLE001 - container gone / stream closed is expected
            pass

    @staticmethod
    def _cpu_percent_of(stats: dict) -> float:
        try:
            cpu = stats.get("cpu_stats") or {}
            precpu = stats.get("precpu_stats") or {}
            delta = ((cpu.get("cpu_usage") or {}).get("total_usage", 0)
                     - (precpu.get("cpu_usage") or {}).get("total_usage", 0))
            sys_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
            if sys_delta > 0 and delta > 0:
                # online_cpus first: percpu_usage is absent on cgroup v2 (R15).
                n = cpu.get("online_cpus")
                if not n:
                    percpu = (cpu.get("cpu_usage") or {}).get("percpu_usage")
                    n = len(percpu) if percpu else 1
                return (delta / sys_delta) * n * 100.0
        except (KeyError, TypeError, ZeroDivisionError):
            pass
        return 0.0

    @property
    def memory_current(self) -> int:
        with self._lock:
            return self._mem_current

    @property
    def memory_peak(self) -> int:
        with self._lock:
            return self._mem_peak

    @property
    def cpu_percent(self) -> float:
        with self._lock:
            return self._cpu_percent


# ---------------------------------------------------------------- executor

class DockerExecutor:
    CMD_MOVE = 0
    CMD_QUIT = 1
    MOVE_NONE = 255
    _RESP = struct.Struct(">BBddd")

    def __init__(
        self,
        bot_code: str,
        *,
        name: str = "bot",
        seed: int | None = None,
        cpu_limit: float = 100.0,
        wall_limit: float = 300.0,
        config: DockerConfig | None = None,
    ):
        self._config = config or DockerConfig()
        self._name = name
        self._cpu_limit = cpu_limit
        self._wall_limit = wall_limit

        self._cpu_time = 0.0
        self._cgroup_cpu = 0.0
        self._bot_wall_time = 0.0
        self._host_wall_time = 0.0
        self._rounds = 0
        self._default_from_round: int | None = None

        self._timed_out = False
        self._errored = False
        self._memory_exceeded = False
        self._error_message: str | None = None
        self._closed = False

        self._client = get_client()
        self._container = None
        self._sock_dir: str | None = None
        self._server: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._monitor: _StatsMonitor | None = None
        self._mem_peak_final = 0
        self._mem_current_final = 0

        self._start(bot_code, seed)

    # -------------------------------------------------------------- startup

    def _start(self, bot_code: str, seed: int | None) -> None:
        self._sock_dir = tempfile.mkdtemp(prefix=SOCK_DIR_PREFIX)
        # mkdtemp is 0700/host-UID; the container is UID 1000 and needs +x to
        # traverse the mount. 0711 grants traversal without listing or writing -
        # 0777 would let any local user swap the socket and drive the match (R5).
        os.chmod(self._sock_dir, 0o711)
        sock_path = os.path.join(self._sock_dir, "ipc.sock")

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(sock_path)
        self._server.listen(1)
        os.chmod(sock_path, 0o666)

        try:
            self._container = self._client.containers.run(
                image=self._config.image,
                command=["python", "-B", "/app/_docker_runner.py", "/ipc/ipc.sock"],
                detach=True,
                mem_limit=self._config.memory_limit,
                memswap_limit=self._config.memory_limit,
                cpu_quota=self._config.cpu_quota,
                cpu_period=self._config.cpu_period,
                pids_limit=self._config.pids_limit,
                network_disabled=True,
                read_only=True,
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                volumes={self._sock_dir: {"bind": "/ipc", "mode": "rw"}},
                # noexec restored (R6): nothing in the package set execs from /tmp.
                tmpfs={"/tmp": f"size={self._config.tmpfs_size},"
                               f"mode=1777,noexec,nosuid,nodev"},
                environment={
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "HOME": "/tmp",
                    "XDG_CACHE_HOME": "/tmp/.cache",
                    "MPLCONFIGDIR": "/tmp/.cache/matplotlib",
                    # Single-threaded BLAS: N threads in a 1-core cgroup is pure
                    # contention, and it makes CPU accounting meaningless.
                    "OMP_NUM_THREADS": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1",
                    "NUMEXPR_NUM_THREADS": "1",
                },
                log_config={"type": "json-file",
                            "config": {"max-size": self._config.log_max_size,
                                       "max-file": "1"}},
                labels={LABEL_ROLE: LABEL_ROLE_VALUE, LABEL_INSTANCE: INSTANCE_ID},
                remove=False,
                user="1000:1000",
            )
        except ImageNotFound as e:
            self._teardown()
            raise MatchExecutionError(
                f"Docker image '{self._config.image}' not found. Build with: "
                f"docker build -t {self._config.image} src/nashium/server/"
            ) from e
        except Exception as e:  # noqa: BLE001
            self._teardown()
            raise MatchExecutionError(f"Failed to start container: {e}") from e

        if self._config.stats_enabled:
            self._monitor = _StatsMonitor(self._container)
            self._monitor.start()

        self._accept_connection()
        self._load_bot(bot_code, seed)

    def _accept_connection(self) -> None:
        deadline = time.monotonic() + self._config.connect_timeout
        self._server.settimeout(1.0)
        while True:
            try:
                self._conn, _ = self._server.accept()
                return
            except socket.timeout:
                if time.monotonic() >= deadline:
                    logs = self._container_logs()
                    self._teardown()
                    raise MatchExecutionError(
                        f"[{self._name}] container never connected within "
                        f"{self._config.connect_timeout:.0f}s. Logs:\n{logs}"
                    )
                try:
                    self._container.reload()
                    if self._container.status in ("exited", "dead"):
                        logs = self._container_logs()
                        self._teardown()
                        raise MatchExecutionError(
                            f"[{self._name}] container exited during startup. "
                            f"Logs:\n{logs}"
                        )
                except MatchExecutionError:
                    raise
                except Exception:  # noqa: BLE001
                    pass

    def _load_bot(self, code: str, seed: int | None) -> None:
        msg = {"cmd": "load", "protocol": PROTOCOL_VERSION, "code": code}
        if seed is not None:
            msg["seed"] = seed
        data = json.dumps(msg).encode()

        self._conn.settimeout(self._config.load_timeout)
        try:
            self._conn.sendall(struct.pack(">I", len(data)) + data)
            raw_len = self._recv_exact(4)
            if raw_len is None:
                self._fault_bot("Bot process exited while loading")
                return
            length = struct.unpack(">I", raw_len)[0]
            if length > MAX_LOAD_RESPONSE:
                # The bot's code runs in the same process as the runner, so a
                # hostile bot can seize the socket. Bound every host-side read.
                self._teardown()
                raise MatchExecutionError(
                    f"[{self._name}] container sent an oversized load response "
                    f"({length} bytes) - protocol violation"
                )
            body = self._recv_exact(length)
            if body is None:
                self._fault_bot("Bot process exited while loading")
                return
            resp = json.loads(body)
        except socket.timeout:
            self._fault_timeout(
                f"Bot failed to initialise within {self._config.load_timeout:.0f}s "
                f"(infinite loop or extremely slow import at module level)"
            )
            return
        except MatchExecutionError:
            raise
        except Exception as e:  # noqa: BLE001
            self._fault_bot(f"Bot load failed: {e}")
            return

        got = resp.get("protocol")
        if got != PROTOCOL_VERSION:
            self._teardown()
            raise MatchExecutionError(
                f"Runner image speaks protocol v{got}, host expects "
                f"v{PROTOCOL_VERSION}. The image is stale - rebuild it:\n"
                f"  docker build -t {self._config.image} src/nashium/server/"
            )

        if resp.get("status") != "ok":
            self._fault_bot(resp.get("error") or "Bot load failed")
            return
        logger.debug("[%s] bot loaded", self._name)

    # ----------------------------------------------------------------- play

    @property
    def faulted(self) -> bool:
        return self._timed_out or self._errored or self._memory_exceeded or self._closed

    def get_move(self, opponent_last_move: int | None) -> int:
        """Return the bot's move, or 0 if it has faulted. Never raises for bot faults."""
        if self.faulted:
            return 0
        if self._billed_cpu >= self._cpu_limit:
            self._fault_timeout(f"CPU budget of {self._cpu_limit:.0f}s exhausted")
            return 0
        if self._host_wall_time >= self._wall_limit:
            self._fault_timeout(f"Wall-clock budget of {self._wall_limit:.0f}s exhausted")
            return 0

        budget = min(self._cpu_limit - self._billed_cpu,
                     self._wall_limit - self._host_wall_time)
        self._conn.settimeout(max(1.0, budget) + 1.0)

        opp = self.MOVE_NONE if opponent_last_move is None else int(opponent_last_move)
        t0 = time.perf_counter()
        try:
            self._conn.sendall(bytes((self.CMD_MOVE, opp)))
            resp = self._recv_exact(self._RESP.size)
        except socket.timeout:
            self._host_wall_time += time.perf_counter() - t0
            self._fault_timeout("Bot did not respond within its remaining time budget")
            return 0
        except (OSError, ConnectionError) as e:
            self._host_wall_time += time.perf_counter() - t0
            self._fault_connection_lost(f"IPC failure: {e}")
            return 0
        self._host_wall_time += time.perf_counter() - t0

        if resp is None:
            self._fault_connection_lost("Bot process exited unexpectedly")
            return 0

        status, move, cpu, wall, cgroup = self._RESP.unpack(resp)
        self._cpu_time += cpu
        self._bot_wall_time += wall
        if cgroup >= 0.0:
            self._cgroup_cpu = max(self._cgroup_cpu, cgroup)
        self._rounds += 1

        if status != 0:
            self._fault_bot(self._container_logs()
                            or "Bot raised an exception in move()")
            return 0
        if move not in (0, 1):
            self._fault_bot(f"Bot returned an invalid move: {move}")
            return 0
        if self._billed_cpu >= self._cpu_limit:
            self._fault_timeout(f"CPU budget of {self._cpu_limit:.0f}s exhausted")
        return move

    @property
    def _billed_cpu(self) -> float:
        """process_time misses child processes; the cgroup counter does not.
        Billing the max closes the subprocess-evasion hole (R12)."""
        return max(self._cpu_time, self._cgroup_cpu)

    # ---------------------------------------------------------------- fault

    def _mark_default_start(self) -> None:
        if self._default_from_round is None:
            self._default_from_round = self._rounds

    def _fault_bot(self, message: str) -> None:
        if self.faulted:
            return
        self._check_oom()
        if self._memory_exceeded:
            self._mark_default_start()
            return
        self._errored = True
        self._error_message = message
        self._mark_default_start()
        logger.debug("[%s] faulted: %s", self._name, message)

    def _fault_timeout(self, message: str) -> None:
        if self.faulted:
            return
        self._timed_out = True
        self._error_message = message
        self._mark_default_start()

    def _fault_connection_lost(self, message: str) -> None:
        if self.faulted:
            return
        self._check_oom()
        if self._memory_exceeded:
            self._mark_default_start()
            return
        self._errored = True
        self._error_message = self._container_logs() or message
        self._mark_default_start()

    def _check_oom(self) -> None:
        if self._container is None:
            return
        try:
            self._container.reload()
            state = (self._container.attrs or {}).get("State") or {}
            if state.get("OOMKilled") or state.get("ExitCode") == 137:
                self._memory_exceeded = True
                self._errored = False
                self._timed_out = False
                self._error_message = "Container exceeded its memory limit (OOM-killed)"
        except Exception:  # noqa: BLE001
            return

    def _container_logs(self, tail: int = 40) -> str:
        try:
            if self._container is None:
                return ""
            raw = self._container.logs(stdout=False, stderr=True, tail=tail)
            return raw.decode("utf-8", errors="replace").strip()[:4000]
        except Exception:  # noqa: BLE001
            return ""

    # ----------------------------------------------------------------- misc

    def _recv_exact(self, n: int) -> bytes | None:
        data = bytearray()
        while len(data) < n:
            chunk = self._conn.recv(n - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    @property
    def name(self) -> str:
        return self._name

    @property
    def elapsed_time(self) -> float:
        return self._billed_cpu

    @property
    def wall_time(self) -> float:
        return self._host_wall_time

    @property
    def rounds_played(self) -> int:
        return self._rounds

    @property
    def default_from_round(self) -> int | None:
        return self._default_from_round

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    @property
    def errored(self) -> bool:
        return self._errored

    @property
    def memory_exceeded(self) -> bool:
        return self._memory_exceeded

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def memory_bytes_peak(self) -> int | None:
        # Cached so the value survives teardown (R9).
        if self._monitor is not None:
            self._mem_peak_final = max(self._mem_peak_final, self._monitor.memory_peak)
        return self._mem_peak_final or None

    @property
    def memory_bytes_current(self) -> int | None:
        if self._monitor is not None:
            self._mem_current_final = self._monitor.memory_current
        return self._mem_current_final or None

    def _teardown(self) -> None:
        if self._monitor is not None:
            self._mem_peak_final = max(self._mem_peak_final, self._monitor.memory_peak)

        # Container first: the stats stream blocks on read and only unblocks
        # when the container disappears (R14).
        if self._container is not None:
            try:
                self._container.remove(force=True)
            except Exception:  # noqa: BLE001
                pass
            self._container = None

        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None

        for sock in (self._conn, self._server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._conn = self._server = None

        if self._sock_dir and os.path.isdir(self._sock_dir):
            shutil.rmtree(self._sock_dir, ignore_errors=True)
        self._sock_dir = None

    def close(self) -> None:
        if self._closed:
            return
        if not self._memory_exceeded:
            self._check_oom()
        if self._conn is not None:
            try:
                self._conn.sendall(bytes((self.CMD_QUIT, 0)))
            except OSError:
                pass
        self._teardown()
        self._closed = True

    def __enter__(self) -> "DockerExecutor":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


# ------------------------------------------------------------------- match

def _snapshot(ex: DockerExecutor, cpu_budget: float, mem_budget: int | None):
    cpu_frac = (ex.elapsed_time / cpu_budget) if cpu_budget else 0.0
    cpu_scaled = int(max(0.0, min(1.0, cpu_frac)) * 1000)
    mem = ex.memory_bytes_current or ex.memory_bytes_peak
    mem_scaled = (int(max(0.0, min(1.0, mem / mem_budget)) * 1000)
                  if (mem_budget and mem) else 0)
    return cpu_scaled, mem_scaled


def _runtime_stats(ex: DockerExecutor, mem_budget: int | None) -> RuntimeStats:
    return RuntimeStats(
        elapsed_time_seconds=ex.elapsed_time,
        wall_seconds=ex.wall_time,
        timed_out=ex.timed_out,
        memory_exceeded=ex.memory_exceeded,
        errored=ex.errored,
        error_message=ex.error_message,
        memory_bytes_peak=ex.memory_bytes_peak,
        memory_bytes_limit=mem_budget,
        rounds_played=ex.rounds_played,
        default_from_round=ex.default_from_round,
    )


def run_match(
    *,
    submitted_code: str,
    leaderboard_code: str,
    seed: int,
    config: MatchConfig,
    docker_config: DockerConfig | None = None,
    capture_history: bool = True,
) -> MatchResult:
    """Run one full match in two sandboxed containers.

    A faulted bot plays 0 for every remaining round and the match still runs to
    completion; wins are counted normally. Raises MatchExecutionError only for
    harness problems, which the caller must treat as blocking.
    """
    docker_config = docker_config or DockerConfig(
        memory_limit=config.max_total_memory_bytes_per_bot)
    mem_budget = parse_memory_limit_bytes(docker_config.memory_limit)
    cpu_budget = config.max_total_time_seconds_per_bot
    wall_budget = config.max_total_wall_seconds_per_bot

    start = time.perf_counter()

    sub_hist: list[int] = []
    lead_raw_hist: list[int] = []
    lead_eff_hist: list[int] = []
    sub_cpu: list[int] = []
    sub_ram: list[int] = []
    lead_cpu: list[int] = []
    lead_ram: list[int] = []
    sample_interval = max(1, int(config.rounds * 0.005))

    with DockerExecutor(submitted_code, name="submitted", seed=seed,
                        cpu_limit=cpu_budget, wall_limit=wall_budget,
                        config=docker_config) as submitted, \
         DockerExecutor(leaderboard_code, name="leaderboard", seed=seed,
                        cpu_limit=cpu_budget, wall_limit=wall_budget,
                        config=docker_config) as leaderboard:

        submitted_wins = 0
        last_submitted: int | None = None
        last_lead_effective: int | None = None

        for rnd in range(1, config.rounds + 1):
            s_move = submitted.get_move(last_lead_effective)
            l_raw = leaderboard.get_move(last_submitted)
            l_move = 1 - l_raw  # inversion makes the game zero-sum

            if s_move == l_move:
                submitted_wins += 1

            if capture_history:
                sub_hist.append(s_move)
                lead_raw_hist.append(l_raw)
                lead_eff_hist.append(l_move)

            last_submitted = s_move
            last_lead_effective = l_move

            if capture_history and (rnd % sample_interval == 0 or rnd == config.rounds):
                c, m = _snapshot(submitted, cpu_budget, mem_budget)
                sub_cpu.append(c)
                sub_ram.append(m)
                c, m = _snapshot(leaderboard, cpu_budget, mem_budget)
                lead_cpu.append(c)
                lead_ram.append(m)

        wall_time = time.perf_counter() - start
        sub_rt = _runtime_stats(submitted, mem_budget)
        lead_rt = _runtime_stats(leaderboard, mem_budget)

    import dataclasses
    sub_rt = dataclasses.replace(sub_rt, cpu_usage_samples=tuple(sub_cpu),
                                 ram_usage_samples=tuple(sub_ram))
    lead_rt = dataclasses.replace(lead_rt, cpu_usage_samples=tuple(lead_cpu),
                                  ram_usage_samples=tuple(lead_ram))

    sub_moves = tuple(sub_hist)
    lead_eff = tuple(lead_eff_hist)

    score: tuple[int, ...] = ()
    deduced: tuple[int, ...] = ()
    s_bot = l_bot = None
    if capture_history:
        score, deduced, s_bot, l_bot = build_derived_match_data(
            submitted_moves=sub_moves, leaderboard_moves_effective=lead_eff)

    return MatchResult(
        seed=seed,
        config=config,
        rounds=config.rounds,
        submitted_wins=submitted_wins,
        submitted_win_rate=submitted_wins / config.rounds if config.rounds else 0.0,
        submitted=sub_rt,
        leaderboard=lead_rt,
        wall_time_seconds=wall_time,
        submitted_moves=sub_moves,
        leaderboard_moves_raw=tuple(lead_raw_hist),
        leaderboard_moves_effective=lead_eff,
        submitted_performance=score,
        leaderboard_output_deduced=deduced,
        submitted_stats=s_bot,
        leaderboard_stats=l_bot,
    )