from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ..core.engine import RoundState
from ..core.errors import BotLoadError, BotRuntimeError, InvalidMoveError


@dataclass
class SandboxConfig:
    """Configuration for sandboxed bot execution."""

    docker_image: str = "nashium-runner:latest"
    memory_limit: str = "256m"
    cpu_limit: float = 1.0
    pids_limit: int = 64
    network_enabled: bool = False
    time_limit: float = 100.0
    read_only_root: bool = True
    # Per-move timeout (prevents infinite loops on single move)
    move_timeout: float = 5.0


class DockerExecutor:
    """Executes a bot inside a Docker container.

    Provides strong isolation and resource limits for untrusted code.

    Usage:
        with DockerExecutor(bot_source_code, config) as executor:
            summary = run_match_with_executors(executor, other_executor, match_config)
    """

    def __init__(
        self,
        bot_code: str,
        config: SandboxConfig | None = None,
        *,
        container_name: str | None = None,
    ):
        self._config = config or SandboxConfig()
        self._elapsed_time = 0.0
        self._timed_out = False
        self._closed = False
        self._process: subprocess.Popen | None = None
        self._container_name = container_name

        self._start_container(bot_code)

    def _build_docker_command(self) -> list[str]:
        """Build the docker run command with security options."""
        cmd = [
            "docker", "run",
            "--rm",
            "-i",
            "--memory", self._config.memory_limit,
            "--memory-swap", self._config.memory_limit,  # Disable swap
            f"--cpus={self._config.cpu_limit}",
            f"--pids-limit={self._config.pids_limit}",
            "--security-opt=no-new-privileges:true",
        ]

        if self._config.read_only_root:
            cmd.append("--read-only")
            # Need a writable /tmp for Python
            cmd.extend(["--tmpfs", "/tmp:size=32m,mode=1777"])

        if not self._config.network_enabled:
            cmd.append("--network=none")

        if self._container_name:
            cmd.extend(["--name", self._container_name])

        cmd.append(self._config.docker_image)
        return cmd

    def _start_container(self, bot_code: str) -> None:
        """Start the Docker container and load the bot."""
        cmd = self._build_docker_command()

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            # Send bot code to load
            self._send({"cmd": "load", "code": bot_code})
            response = self._recv(timeout=10.0)

            if response.get("status") != "ok":
                error = response.get("error", "Unknown error loading bot")
                raise BotLoadError(f"Failed to load bot: {error}")

        except FileNotFoundError:
            raise BotLoadError("Docker is not installed or not in PATH")
        except BotLoadError:
            self.close()
            raise
        except Exception as e:
            self.close()
            raise BotLoadError(f"Failed to start container: {e}")

    def _send(self, data: dict) -> None:
        """Send JSON message to container stdin."""
        if self._process is None or self._process.stdin is None:
            raise BotRuntimeError("Container not running", RuntimeError())

        try:
            line = json.dumps(data) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except BrokenPipeError as e:
            raise BotRuntimeError("Container process terminated", e)

    def _recv(self, timeout: float | None = None) -> dict:
        """Receive JSON message from container stdout."""
        if self._process is None or self._process.stdout is None:
            raise BotRuntimeError("Container not running", RuntimeError())

        import select

        timeout = timeout or self._config.move_timeout

        # Use select for timeout on Unix, fall back to blocking on Windows
        if hasattr(select, "select"):
            ready, _, _ = select.select([self._process.stdout], [], [], timeout)
            if not ready:
                self.close()
                raise BotRuntimeError(
                    f"Container timed out after {timeout}s", TimeoutError()
                )

        line = self._process.stdout.readline()

        if not line:
            stderr = ""
            if self._process.stderr:
                stderr = self._process.stderr.read()
            raise BotRuntimeError(
                f"Container terminated unexpectedly: {stderr.strip()}", RuntimeError()
            )

        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise BotRuntimeError(f"Invalid JSON from container: {line!r}", e)

    def get_move(self, state: RoundState) -> int:
        """Get the bot's move for the given state."""
        if self._timed_out:
            return 0

        if self._closed:
            raise BotRuntimeError("Executor is closed", RuntimeError())

        self._send({
            "cmd": "move",
            "round_index": state.round_index,
            "my_history": list(state.my_history),
            "opponent_history": list(state.opponent_history),
        })

        response = self._recv()

        if response.get("status") == "error":
            raise BotRuntimeError(response.get("error", "Unknown error"), RuntimeError())

        move = response.get("move", 0)
        elapsed = response.get("time", 0.0)
        self._elapsed_time += elapsed

        if self._elapsed_time > self._config.time_limit:
            self._timed_out = True

        if move not in (0, 1):
            raise InvalidMoveError(
                f"Bot returned invalid move {move!r} on round {state.round_index}. "
                "Expected 0 or 1."
            )

        return move

    @property
    def elapsed_time(self) -> float:
        return self._elapsed_time

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def close(self) -> None:
        """Terminate the container and clean up resources."""
        if self._closed:
            return

        self._closed = True

        if self._process is None:
            return

        # Try graceful shutdown
        try:
            if self._process.stdin:
                self._process.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                self._process.stdin.flush()
            self._process.wait(timeout=1)
        except:
            pass

        # Force kill if needed
        try:
            self._process.terminate()
            self._process.wait(timeout=1)
        except:
            pass

        try:
            self._process.kill()
            self._process.wait(timeout=1)
        except:
            pass

    def __enter__(self) -> "DockerExecutor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class SubprocessExecutor:
    """Executes a bot in a subprocess without Docker.

    Less secure than DockerExecutor but useful for testing
    or environments without Docker.
    """

    def __init__(
        self,
        bot_code: str,
        time_limit: float = float("inf"),
        python_executable: str | None = None,
    ):
        self._time_limit = time_limit
        self._elapsed_time = 0.0
        self._timed_out = False
        self._closed = False
        self._python = python_executable or "python"

        self._start_process(bot_code)

    def _start_process(self, bot_code: str) -> None:
        """Start subprocess with the runner script."""
        runner_path = Path(__file__).parent / "_subprocess_runner.py"

        self._process = subprocess.Popen(
            [self._python, "-u", str(runner_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._send({"cmd": "load", "code": bot_code})
        response = self._recv()

        if response.get("status") != "ok":
            self.close()
            raise BotLoadError(response.get("error", "Failed to load bot"))

    def _send(self, data: dict) -> None:
        if self._process.stdin:
            self._process.stdin.write(json.dumps(data) + "\n")
            self._process.stdin.flush()

    def _recv(self) -> dict:
        if self._process.stdout:
            line = self._process.stdout.readline()
            if line:
                return json.loads(line)
        return {"status": "error", "error": "Process terminated"}

    def get_move(self, state: RoundState) -> int:
        if self._timed_out:
            return 0

        self._send({
            "cmd": "move",
            "round_index": state.round_index,
            "my_history": list(state.my_history),
            "opponent_history": list(state.opponent_history),
        })

        response = self._recv()

        if response.get("status") == "error":
            raise BotRuntimeError(response.get("error", "Unknown"), RuntimeError())

        move = response.get("move", 0)
        self._elapsed_time += response.get("time", 0.0)

        if self._elapsed_time > self._time_limit:
            self._timed_out = True

        return move

    @property
    def elapsed_time(self) -> float:
        return self._elapsed_time

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._process.terminate()
            self._process.wait(timeout=1)
        except:
            self._process.kill()

    def __enter__(self) -> "SubprocessExecutor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()