from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from nashium.core import RoundState, BotLoadError, InvalidMoveError
from nashium.core.errors import BotRuntimeError


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


class SubprocessExecutor:
    """
    Executes a bot in a subprocess.

    Uses delta-based protocol to minimize IPC overhead.
    Uses thread-based timeout for reliable cross-platform behavior.
    """

    def __init__(
            self,
            bot_code: str,
            time_limit: float = 100.0,
            seed: int | None = None,
            python_executable: str | None = None,
    ):
        self._time_limit = time_limit
        self._elapsed_time = 0.0
        self._timed_out = False
        self._closed = False
        self._python = python_executable or sys.executable
        self._process: subprocess.Popen | None = None
        self._seed = seed

        self._response_queue: queue.Queue = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()

        self._start_process(bot_code)

    def _start_process(self, bot_code: str) -> None:
        """Start subprocess with the runner script."""
        runner_path = Path(__file__).parent / "_subprocess_runner.py"

        if not runner_path.exists():
            raise FileNotFoundError(f"Runner script not found: {runner_path}")

        kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
        }

        if hasattr(os, "setsid"):
            kwargs["start_new_session"] = True

        self._process = subprocess.Popen(
            [self._python, "-u", str(runner_path)],
            **kwargs
        )

        self._start_reader_thread()

        # Load bot with optional seed
        load_cmd = {"cmd": "load", "code": bot_code}
        if self._seed is not None:
            load_cmd["seed"] = self._seed

        self._send(load_cmd)
        response = self._recv(timeout=10.0)

        if response.get("status") != "ok":
            error_msg = response.get("error", "Failed to load bot")
            self.close()
            raise BotLoadError(error_msg)

    def _start_reader_thread(self) -> None:
        """Start background thread that reads from subprocess stdout."""

        def reader_loop():
            while not self._stop_reader.is_set():
                if self._process is None or self._process.stdout is None:
                    break

                try:
                    line = self._process.stdout.readline()
                    if not line:
                        self._response_queue.put(("eof", None))
                        break
                    self._response_queue.put(("ok", line))
                except Exception as e:
                    self._response_queue.put(("error", str(e)))
                    break

        self._reader_thread = threading.Thread(target=reader_loop, daemon=True)
        self._reader_thread.start()

    def _send(self, data: dict) -> None:
        """Send JSON message to subprocess."""
        if self._process is None or self._process.stdin is None:
            raise BotRuntimeError("Process not running", RuntimeError())

        try:
            line = json.dumps(data) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self._kill_process()
            raise BotRuntimeError("Process terminated unexpectedly", e)

    def _recv(self, timeout: float | None = None) -> dict:
        """Receive JSON message from subprocess with timeout."""
        if timeout is None:
            raise ValueError("_recv() requires an explicit timeout")

        try:
            status, data = self._response_queue.get(timeout=timeout)
        except queue.Empty:
            self._kill_process()
            self._timed_out = True
            raise BotRuntimeError(
                f"Move timed out after {timeout}s - process killed",
                TimeoutError()
            )

        if status == "eof":
            stderr = self._get_stderr()
            self._kill_process()
            raise BotRuntimeError(
                f"Process terminated unexpectedly. stderr: {stderr}",
                RuntimeError()
            )

        if status == "error":
            self._kill_process()
            raise BotRuntimeError(f"Read error: {data}", RuntimeError())

        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            raise BotRuntimeError(f"Invalid JSON from process: {data!r}", e)

    def _get_stderr(self) -> str:
        """Get stderr output from process (non-blocking)."""
        if self._process is None or self._process.stderr is None:
            return ""
        try:
            import select
            if hasattr(select, "select"):
                ready, _, _ = select.select([self._process.stderr], [], [], 0.1)
                if ready:
                    return self._process.stderr.read(4096)
            return ""
        except:
            return ""

    def _kill_process(self) -> None:
        """Forcefully kill the subprocess."""
        if self._process is None:
            return

        self._stop_reader.set()
        pid = self._process.pid

        try:
            self._process.terminate()
        except:
            pass

        try:
            self._process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass

        try:
            self._process.kill()
        except:
            pass

        if hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass

        try:
            self._process.wait(timeout=1)
        except:
            pass

        for stream in [self._process.stdin, self._process.stdout, self._process.stderr]:
            if stream:
                try:
                    stream.close()
                except:
                    pass

    def get_move(self, opponent_last_move: int | None) -> int:
        """Get the bot's next move."""
        if self._timed_out:
            return 0

        if self._closed:
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
            raise InvalidMoveError(
                f"Bot returned invalid move {move!r}. Expected 0 or 1."
            )

        return move

    def reset(self) -> None:
        """Reset the bot state for a new match."""
        if self._closed or self._process is None:
            return

        try:
            self._send({"cmd": "reset"})
            self._recv(timeout=1.0)
        except:
            pass

        self._elapsed_time = 0.0
        self._timed_out = False

    @property
    def elapsed_time(self) -> float:
        return self._elapsed_time

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def close(self) -> None:
        """Terminate the subprocess and clean up resources."""
        if self._closed:
            return

        self._closed = True
        self._stop_reader.set()

        if self._process is None:
            return

        try:
            if self._process.stdin and not self._process.stdin.closed:
                self._process.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                self._process.stdin.flush()
            self._process.wait(timeout=0.5)
        except:
            pass

        self._kill_process()

    def __enter__(self) -> "SubprocessExecutor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()