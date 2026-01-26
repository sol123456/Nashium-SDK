# fast_executor.py
from __future__ import annotations

import json
import os
import socket
import struct
import tempfile
import shutil
import time
from dataclasses import dataclass

from nashium.core.errors import BotLoadError, BotRuntimeError, InvalidMoveError

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


class DockerExecutor:
    """
    Fast executor using Unix socket with binary protocol.
    Eliminates Docker stream demux, JSON per-move, and threading overhead.
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
        if self._container is None:
            return

        try:
            stats = self._container.stats(stream=False)
        except Exception:
            return

        mem_stats = stats.get("memory_stats") or {}
        current = mem_stats.get("usage")
        if isinstance(current, int):
            self._memory_bytes_current = current
        peak = mem_stats.get("max_usage")
        if peak is None:
            peak = current
        if isinstance(peak, int):
            if self._memory_bytes_peak is None or peak > self._memory_bytes_peak:
                self._memory_bytes_peak = peak

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
                command=["python", "/app/_subprocess_runner.py", "/ipc/ipc.sock"],
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

        # Wait for container to connect
        print(f"  [{self._name}] Loading bot...", end="", flush=True)

        self._server.settimeout(10.0)
        try:
            self._conn, _ = self._server.accept()
            #allows docker at least 10 seconds to load properly
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
                self.poll_usage()
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
        return self._memory_bytes_peak

    @property
    def memory_bytes_current(self) -> int | None:
        return self._memory_bytes_current

    @property
    def memory_exceeded(self) -> bool:
        return self._memory_exceeded

    @property
    def errored(self) -> bool:
        return self._errored

    @property
    def error_message(self) -> str | None:
        return self._error_message

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