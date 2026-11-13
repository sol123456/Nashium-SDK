"""Console output for the farm supervisor.

Design goals:
  * one line per worker event, prefixed ``[w1]`` so six interleaved streams stay
    readable;
  * colours only when the terminal can take them (and ``NO_COLOR`` is respected);
  * a redrawn status block in ``--quiet`` mode;
  * nothing but plain ASCII, so Windows consoles with legacy code pages cope.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from typing import IO, Optional, Sequence

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"

# One colour per worker, cycled. Chosen to be distinguishable on both dark and
# light terminals.
WORKER_COLORS = [CYAN, GREEN, YELLOW, MAGENTA, BLUE, "\x1b[96m", "\x1b[92m", "\x1b[95m"]


def _enable_windows_ansi() -> None:
    """Turn on VT processing so ANSI codes work in the Windows console."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        # Not a real console (e.g. redirected to a file) - colours get disabled
        # by the isatty check anyway.
        pass


class Console:
    """Thread-safe writer for multiplexed worker output."""

    def __init__(self, quiet: bool = False, color: Optional[bool] = None, stream: Optional[IO[str]] = None):
        self.stream = stream or sys.stdout
        self.quiet = quiet
        if color is None:
            color = (
                not os.environ.get("NO_COLOR")
                and not os.environ.get("NASHIUM_NO_COLOR")
                and bool(getattr(self.stream, "isatty", lambda: False)())
            )
        self.color = bool(color)
        self.interactive = bool(getattr(self.stream, "isatty", lambda: False)())
        if self.color:
            _enable_windows_ansi()
        self._lock = threading.Lock()
        self._dashboard_lines = 0

    # ------------------------------------------------------------- primitives

    def _paint(self, text: str, code: str) -> str:
        return f"{code}{text}{RESET}" if self.color else text

    def _clear_dashboard_locked(self) -> None:
        if self._dashboard_lines and self.interactive:
            # Cursor to column 1, N lines up, erase to end of screen.
            self.stream.write(f"\x1b[{self._dashboard_lines}F\x1b[J")
            self.stream.flush()
        self._dashboard_lines = 0

    def _write_locked(self, text: str) -> None:
        self._clear_dashboard_locked()
        self.stream.write(text + "\n")
        self.stream.flush()

    # ----------------------------------------------------------------- public

    def worker_line(self, index: int, text: str, level: str = "") -> None:
        """One line of a worker's own output."""
        if self.quiet:
            return
        prefix = self._paint(f"[w{index}]", WORKER_COLORS[(index - 1) % len(WORKER_COLORS)])
        body = text
        if level == "ERROR":
            body = self._paint(text, RED)
        elif level == "WARNING":
            body = self._paint(text, YELLOW)
        with self._lock:
            self._write_locked(f"{prefix} {body}")

    def system(self, text: str) -> None:
        """A line from the supervisor itself."""
        with self._lock:
            self._write_locked(self._paint(f"* {text}", BOLD))

    def info(self, text: str) -> None:
        with self._lock:
            self._write_locked(self._paint(text, DIM))

    def ok(self, text: str) -> None:
        with self._lock:
            self._write_locked(f"{self._paint('[ ok ]', GREEN)} {text}")

    def warn(self, text: str) -> None:
        with self._lock:
            self._write_locked(f"{self._paint('[warn]', YELLOW)} {text}")

    def fail(self, text: str) -> None:
        with self._lock:
            self._write_locked(f"{self._paint('[fail]', RED)} {text}")

    def block(self, lines: Sequence[str]) -> None:
        """Print several pre-formatted lines atomically."""
        with self._lock:
            self._clear_dashboard_locked()
            for line in lines:
                self.stream.write(line + "\n")
            self.stream.flush()

    # -------------------------------------------------------------- dashboard

    def dashboard(self, header: str, rows: Sequence[str]) -> None:
        """Redraw the status block (``--quiet`` mode)."""
        if not self.interactive:
            # Output is redirected to a file/pipe: a redrawn block would just be
            # thousands of duplicate lines, so print nothing.
            return
        with self._lock:
            self._clear_dashboard_locked()
            self.stream.write(header + "\n")
            for row in rows:
                self.stream.write(row + "\n")
            self.stream.flush()
            self._dashboard_lines = 1 + len(rows)

    def close_dashboard(self) -> None:
        with self._lock:
            self._clear_dashboard_locked()


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def terminal_width(default: int = 100) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


def now_stamp() -> str:
    return time.strftime("%H:%M:%S")
