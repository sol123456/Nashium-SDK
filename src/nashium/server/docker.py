from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass

from nashium.core.errors import BotLoadError, BotRuntimeError, InvalidMoveError

try:
    import docker
    from docker.models.containers import Container

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False


@dataclass
class DockerConfig:
    """Configuration for Docker execution."""
    image: str = "nashium-runner:latest"
    memory_limit: str = "256m"
    cpu_quota: int = 50000  # 50% of one CPU
    cpu_period: int = 100000
    pids_limit: int = 64
    move_timeout: float = 5.0


class DockerExecutor:
    """
    Executes a bot in an isolated Docker container.

    Same interface as SubprocessExecutor - uses delta protocol
    (opponent_last_move) for efficient communication.
    """

    def __init__(
            self,
            bot_code: str,
            time_limit: float = 100.0,
            move_timeout: float = 5.0,
            seed: int | None = None,
            config: DockerConfig | None = None,
    ):
        if not DOCKER_AVAILABLE:
            raise RuntimeError(
                "DockerExecutor requires 'docker' package. "
                "Install with: pip install docker"
            )

        self._config = config or DockerConfig()
        self._config = DockerConfig(
            image=self._config.image,
            memory_limit=self._config.memory_limit,
            cpu_quota=self._config.cpu_quota,
            cpu_period=self._config.cpu_period,
            pids_limit=self._config.pids_limit,
            move_timeout=move_timeout,
        )

        self._bot_code = bot_code
        self._seed = seed
        self._time_limit = time_limit
        self._elapsed_time = 0.0
        self._timed_out = False
        self._closed = False

        self._client = docker.from_env()
        self._container: Container | None = None
        self._socket = None

        self._response_queue: queue.Queue = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()

        self._start_container()

    def _start_container(self) -> None:
        """Start Docker container and load bot."""
        try:
            self._container = self._client.containers.run(
                image=self._config.image,
                detach=True,
                stdin_open=True,
                mem_limit=self._config.memory_limit,
                cpu_quota=self._config.cpu_quota,
                cpu_period=self._config.cpu_period,
                pids_limit=self._config.pids_limit,
                network_disabled=True,
                read_only=True,
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                remove=False,
            )
        except docker.errors.ImageNotFound:
            raise RuntimeError(
                f"Docker image '{self._config.image}' not found.\n"
                f"Build with: docker build -t {self._config.image} src/nashium/server/"
            )
        except docker.errors.APIError as e:
            raise RuntimeError(f"Docker API error: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to start container: {e}")

        # Attach socket for bidirectional communication
        self._socket = self._container.attach_socket(
            params={"stdout": 1, "stderr": 1, "stdin": 1, "stream": 1}
        )

        self._start_reader_thread()

        # Load bot code
        load_cmd = {"cmd": "load", "code": self._bot_code}
        if self._seed is not None:
            load_cmd["seed"] = self._seed

        self._send(load_cmd)
        response = self._recv(timeout=10.0)

        if response.get("status") != "ok":
            error_msg = response.get("error", "Failed to load bot")
            self.close()
            raise BotLoadError(error_msg)

    def _start_reader_thread(self) -> None:
        """Background thread that reads container output."""

        def reader_loop():
            buffer = b""
            while not self._stop_reader.is_set():
                try:
                    chunk = self._socket._sock.recv(4096)
                    if not chunk:
                        self._response_queue.put(("eof", None))
                        break

                    buffer += chunk

                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)

                        # Docker multiplexed stream has 8-byte header
                        if len(line) >= 8 and line[0:1] in (b"\x01", b"\x02"):
                            line = line[8:]

                        decoded = line.decode("utf-8").strip()
                        if decoded:
                            self._response_queue.put(("ok", decoded))

                except Exception as e:
                    self._response_queue.put(("error", str(e)))
                    break

        self._reader_thread = threading.Thread(target=reader_loop, daemon=True)
        self._reader_thread.start()

    def _send(self, data: dict) -> None:
        """Send JSON command to container."""
        if self._socket is None:
            raise BotRuntimeError("Container not running", RuntimeError())

        try:
            line = json.dumps(data) + "\n"
            self._socket._sock.send(line.encode())
        except Exception as e:
            self._kill_container()
            raise BotRuntimeError("Failed to send to container", e)

    def _recv(self, timeout: float | None = None) -> dict:
        """Receive JSON response from container."""
        timeout = timeout if timeout is not None else self._config.move_timeout

        try:
            status, data = self._response_queue.get(timeout=timeout)
        except queue.Empty:
            self._kill_container()
            self._timed_out = True
            raise BotRuntimeError(
                f"Move timed out after {timeout}s - container killed",
                TimeoutError()
            )

        if status == "eof":
            logs = self._get_logs()
            self._kill_container()
            raise BotRuntimeError(f"Container terminated. Logs: {logs}", RuntimeError())

        if status == "error":
            self._kill_container()
            raise BotRuntimeError(f"Read error: {data}", RuntimeError())

        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            raise BotRuntimeError(f"Invalid JSON: {data!r}", e)

    def _get_logs(self) -> str:
        """Get container logs for debugging."""
        if self._container is None:
            return ""
        try:
            return self._container.logs(tail=50).decode()
        except:
            return ""

    def _kill_container(self) -> None:
        """Stop and remove container."""
        if self._container is None:
            return

        self._stop_reader.set()

        try:
            self._container.stop(timeout=1)
        except:
            pass

        try:
            self._container.kill()
        except:
            pass

        try:
            self._container.remove(force=True)
        except:
            pass

    def get_move(self, opponent_last_move: int | None) -> int:
        """
        Get bot's next move.

        Same interface as SubprocessExecutor.
        """
        if self._timed_out or self._closed:
            return 0

        if self._elapsed_time > self._time_limit:
            self._timed_out = True
            return 0

        msg = {"cmd": "move"}
        if opponent_last_move is not None:
            msg["opponent_last"] = opponent_last_move

        try:
            self._send(msg)
            response = self._recv()
        except BotRuntimeError:
            self._timed_out = True
            return 0

        if response.get("status") == "error":
            raise BotRuntimeError(response.get("error", "Unknown error"), RuntimeError())

        move = response.get("move", 0)
        elapsed = response.get("time", 0.0)
        self._elapsed_time += elapsed

        if self._elapsed_time > self._time_limit:
            self._timed_out = True

        if move not in (0, 1):
            raise InvalidMoveError(f"Invalid move: {move}")

        return move

    @property
    def elapsed_time(self) -> float:
        return self._elapsed_time

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def close(self) -> None:
        """Clean up container."""
        if self._closed:
            return

        self._closed = True

        if self._container:
            try:
                self._send({"cmd": "quit"})
            except:
                pass

        self._kill_container()

    def __enter__(self) -> "DockerExecutor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()