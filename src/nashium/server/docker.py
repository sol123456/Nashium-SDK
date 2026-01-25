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
    cpu_quota: int = 50000
    cpu_period: int = 100000
    pids_limit: int = 64
    move_timeout: float = 5.0


class DockerExecutor:
    """
    Executes a bot in an isolated Docker container.
    """

    def __init__(
            self,
            bot_code: str,
            time_limit: float = 100.0,
            move_timeout: float = 5.0,
            seed: int | None = None,
            config: DockerConfig | None = None,
            name: str = "Bot",
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
        self._errored = False
        self._error_message: str | None = None
        self._closed = False
        self._name = name

        self._client = docker.from_env()
        self._container: Container | None = None
        self._socket = None
        self._raw_socket = None

        self._response_queue: queue.Queue = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()

        self._start_container()

    def _get_raw_socket(self, socket):
        """Get the raw socket, handling platform differences."""
        if hasattr(socket, '_sock'):
            return socket._sock
        return socket

    def _start_container(self) -> None:
        """Start Docker container and load bot."""
        print(f"  [{self._name}] Starting container...", end="", flush=True)

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
            print(" FAILED", flush=True)
            raise RuntimeError(
                f"Docker image '{self._config.image}' not found.\n"
                f"Build with: docker build -t {self._config.image} src/nashium/server/"
            )
        except docker.errors.APIError as e:
            print(" FAILED", flush=True)
            raise RuntimeError(f"Docker API error: {e}")
        except Exception as e:
            print(" FAILED", flush=True)
            raise RuntimeError(f"Failed to start container: {e}")

        print(" started", flush=True)

        # Attach socket
        self._socket = self._container.attach_socket(
            params={"stdout": 1, "stderr": 1, "stdin": 1, "stream": 1}
        )
        self._raw_socket = self._get_raw_socket(self._socket)

        self._start_reader_thread()

        # Load bot code
        print(f"  [{self._name}] Loading bot...", end="", flush=True)

        load_cmd = {"cmd": "load", "code": self._bot_code}
        if self._seed is not None:
            load_cmd["seed"] = self._seed

        self._send(load_cmd)
        response = self._recv(timeout=10.0)

        if response.get("status") != "ok":
            print(" FAILED", flush=True)
            error_msg = response.get("error", "Failed to load bot")
            self.close()
            raise BotLoadError(error_msg)

        print(" ready ✓", flush=True)

    def _start_reader_thread(self) -> None:
        """Background thread that reads and demultiplexes container output."""
        def reader_loop():
            raw_buffer = b""
            content_buffer = b""

            while not self._stop_reader.is_set():
                try:
                    chunk = self._raw_socket.recv(4096)
                    if not chunk:
                        self._response_queue.put(("eof", None))
                        break

                    raw_buffer += chunk

                    while len(raw_buffer) >= 8:
                        stream_type = raw_buffer[0]
                        padding = raw_buffer[1:4]

                        if stream_type in (1, 2) and padding == b"\x00\x00\x00":
                            payload_size = int.from_bytes(raw_buffer[4:8], 'big')
                            frame_size = 8 + payload_size

                            if len(raw_buffer) < frame_size:
                                break

                            content_buffer += raw_buffer[8:frame_size]
                            raw_buffer = raw_buffer[frame_size:]
                        else:
                            content_buffer += raw_buffer[:1]
                            raw_buffer = raw_buffer[1:]

                    while b"\n" in content_buffer:
                        line, content_buffer = content_buffer.split(b"\n", 1)
                        decoded = line.decode("utf-8").strip()
                        if decoded:
                            self._response_queue.put(("ok", decoded))

                except Exception as e:
                    if not self._stop_reader.is_set():
                        self._response_queue.put(("error", str(e)))
                    break

        self._reader_thread = threading.Thread(target=reader_loop, daemon=True)
        self._reader_thread.start()

    def _send(self, data: dict) -> None:
        """Send JSON command to container."""
        if self._raw_socket is None:
            raise BotRuntimeError("Container not running", RuntimeError())

        try:
            line = json.dumps(data) + "\n"
            self._raw_socket.send(line.encode())
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

        if self._socket is not None:
            try:
                self._socket.close()
            except:
                pass

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
        """Get bot's next move."""
        if self._timed_out or self._errored or self._closed:
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
        except BotRuntimeError as e:
            if isinstance(e.__cause__, TimeoutError):
                self._timed_out = True
            else:
                self._errored = True
                self._error_message = str(e)
            return 0

        if response.get("status") == "error":
            self._errored = True
            self._error_message = response.get("error", "Unknown error")
            return 0

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

    @property
    def errored(self) -> bool:
        return self._errored

    @property
    def error_message(self) -> str | None:
        return self._error_message

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