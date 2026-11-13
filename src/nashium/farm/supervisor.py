"""Supervises N worker processes.

Why separate processes and not a thread pool inside one process?

* ``Worker.run_single`` is a blocking poll/execute/submit cycle, so N workers
  in one process would just be N threads fighting over the GIL for the Python
  parts and over the docker socket for the rest;
* one worker crashing, or leaking a container, must not take the other five
  down with it;
* a child process is the same thing the user runs by hand today, so results and
  logs are identical, and ``docker stats`` shows one process tree per worker.

Each child gets its own log file plus a ``[w3]`` prefix on the shared console.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Optional

from . import preflight
from .config import FarmConfig
from .display import Console, human_duration, now_stamp

_CLAIM_RE = re.compile(r"Claimed interaction (\d+): (.*)$")
_SUBMITTED_RE = re.compile(r"Successfully submitted interaction (\d+)")
_AUTH_RE = re.compile(r"Authentication failed|Invalid worker token|worker token is required")
_ERROR_RE = re.compile(r"\[ERROR\]|Unexpected error")
# "2026-11-13 10:10:27,123 [INFO] nashium.worker.runner: message"
# -> ("INFO", "10:10:27 message"), so six interleaved workers stay readable.
_LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}),\d{3} "
    r"\[(DEBUG|INFO|WARNING|ERROR)\] [\w\.]+: (.*)$"
)


def compact_log_line(line: str) -> tuple[str, str]:
    """Return ``(level, text)``; lines that are not log records pass through."""
    match = _LOG_LINE_RE.match(line)
    if match:
        return match.group(2), f"{match.group(1)} {match.group(3)}"
    return "", line

# A worker that stayed alive this long before dying gets its restart counter reset.
HEALTHY_RUN_SECONDS = 300.0
BACKOFF_START = 5.0
BACKOFF_MAX = 60.0


@dataclass
class WorkerSlot:
    index: int
    process: Optional[subprocess.Popen] = None
    log_path: Optional[Path] = None
    log_file: Optional[IO[str]] = None
    reader: Optional[threading.Thread] = None
    pid: Optional[int] = None
    started_at: float = 0.0

    matches: int = 0
    claims: int = 0
    errors: int = 0
    restarts: int = 0
    exit_code: Optional[int] = None

    status: str = "pending"  # pending | starting | running | backoff | exited | stopped
    last_line: str = ""
    last_activity: float = field(default_factory=time.time)
    current_match: str = ""

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


class Supervisor:
    def __init__(self, config: FarmConfig, console: Console):
        self.config = config
        self.console = console
        self.slots = [WorkerSlot(index=i + 1) for i in range(config.workers)]

        self._write_lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._force_requested = threading.Event()
        self._fatal_reason: Optional[str] = None
        self._run_dir: Optional[Path] = None
        self.started_at = time.time()
        self._dashboard_tick = 0.0

        if config.log_dir:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self._run_dir = Path(config.log_dir).expanduser() / f"farm-{stamp}"

    # ------------------------------------------------------------------ setup

    def _prepare_log_dir(self) -> None:
        if self._run_dir is None:
            return
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self.console.info(f"Logs: {self._run_dir.resolve()}")

    def install_signal_handlers(self) -> None:
        def handler(signum, frame):  # noqa: ARG001
            if self._stop_requested.is_set():
                self._force_requested.set()
                self.console.warn("Second interrupt - killing workers now")
            else:
                self._stop_requested.set()
                self.console.warn(
                    "Stopping after the current match (Ctrl-C again to kill immediately)"
                )

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, handler)
                except (OSError, ValueError):
                    pass

    # ----------------------------------------------------------------- spawn

    def _spawn(self, slot: WorkerSlot) -> None:
        argv = self.config.child_argv()
        env = self.config.child_env()

        popen_kwargs: dict = dict(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        if os.name == "nt":
            # Own process group so CTRL_BREAK_EVENT goes to the worker, not to us.
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Own session so the terminal's Ctrl-C is handled by the supervisor
            # (and we can drain in-flight matches instead of killing them).
            popen_kwargs["start_new_session"] = True

        if self._run_dir is not None:
            slot.log_path = self._run_dir / f"worker-{slot.index:02d}.log"
            slot.log_file = open(slot.log_path, "a", encoding="utf-8", errors="replace", buffering=1)
            slot.log_file.write(f"# farm worker {slot.index} started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            slot.log_file.write("# " + " ".join(argv) + "\n")

        try:
            slot.process = subprocess.Popen(argv, **popen_kwargs)
        except OSError as exc:
            slot.status = "exited"
            slot.exit_code = -1
            self.console.fail(f"could not start worker {slot.index}: {exc}")
            return

        slot.pid = slot.process.pid
        slot.started_at = time.time()
        slot.last_activity = slot.started_at
        slot.status = "running"
        slot.exit_code = None

        slot.reader = threading.Thread(
            target=self._pump, args=(slot,), name=f"farm-reader-{slot.index}", daemon=True
        )
        slot.reader.start()

    def _pump(self, slot: WorkerSlot) -> None:
        """Read one worker's merged stdout/stderr until it exits."""
        stream = slot.process.stdout
        if stream is None:
            return
        try:
            for raw in stream:
                line = raw.rstrip("\r\n")
                try:
                    self._handle_line(slot, line)
                except Exception:
                    # Never let formatting kill the reader thread.
                    pass
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _handle_line(self, slot: WorkerSlot, line: str) -> None:
        slot.last_activity = time.time()
        slot.last_line = line

        if slot.log_file is not None:
            try:
                with self._write_lock:
                    slot.log_file.write(line + "\n")
                    slot.log_file.flush()
            except (ValueError, OSError):
                pass

        claim = _CLAIM_RE.search(line)
        if claim:
            slot.claims += 1
            slot.current_match = claim.group(2).strip()

        if _SUBMITTED_RE.search(line):
            slot.matches += 1
            slot.current_match = ""

        if _ERROR_RE.search(line):
            slot.errors += 1

        if _AUTH_RE.search(line) and self._fatal_reason is None:
            self._fatal_reason = "the server rejected the worker token (HTTP 401)"
            self._stop_requested.set()

        level, text = compact_log_line(line)
        if level == "DEBUG":
            return  # too chatty for the shared console; still in the log file
        self.console.worker_line(slot.index, text, level)

    # ---------------------------------------------------------------- stopping

    def _signal_children(self, force: bool = False) -> None:
        for slot in self.slots:
            if not slot.alive:
                continue
            process = slot.process
            assert process is not None
            try:
                if force:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    if os.name == "nt":
                        # Workers handle SIGBREAK (see note in nashium/worker/main.py).
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        process.send_signal(signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    def _reap(self, slot: WorkerSlot) -> None:
        if slot.process is None:
            return
        try:
            slot.exit_code = slot.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                slot.process.kill()
                slot.exit_code = slot.process.wait(timeout=5)
            except Exception:
                slot.exit_code = -9
        if slot.reader is not None:
            slot.reader.join(timeout=2.0)
        if slot.log_file is not None:
            try:
                slot.log_file.close()
            except Exception:
                pass
            slot.log_file = None
        if slot.status not in ("stopping", "stopped"):
            slot.status = "exited"

    def _shutdown(self, deadline_seconds: float) -> None:
        """Ask children to finish, then force anything still running."""
        self.console.system("Stopping workers - waiting for in-flight matches to finish")
        for slot in self.slots:
            if slot.alive:
                slot.status = "stopping"
        self._signal_children(force=False)

        deadline = time.time() + deadline_seconds
        while time.time() < deadline:
            if self._force_requested.is_set():
                break
            if not any(slot.alive for slot in self.slots):
                break
            time.sleep(0.2)

        stuck = [slot for slot in self.slots if slot.alive]
        if stuck:
            reason = "forced" if self._force_requested.is_set() else f"{int(deadline_seconds)}s elapsed"
            self.console.warn(f"killing {len(stuck)} worker(s) that did not stop ({reason})")
            self._signal_children(force=True)
            for slot in stuck:
                self._reap(slot)
                if slot.status != "exited":
                    slot.status = "stopped"

        for slot in self.slots:
            if slot.process is not None and slot.exit_code is None:
                self._reap(slot)
            if slot.status in ("running", "stopping"):
                slot.status = "stopped"
            elif slot.status == "backoff":
                slot.status = "exited"

    # ---------------------------------------------------------------- dashboard

    def _render_dashboard(self) -> None:
        elapsed = time.time() - self.started_at
        running = sum(1 for slot in self.slots if slot.alive)
        total_matches = sum(slot.matches for slot in self.slots)
        total_errors = sum(slot.errors for slot in self.slots)
        rate = (total_matches / elapsed * 60.0) if elapsed > 0 else 0.0

        header = (
            f"{now_stamp()}  workers {running}/{len(self.slots)}  "
            f"matches {total_matches}  errors {total_errors}  "
            f"{rate:.1f}/min  up {human_duration(elapsed)}"
        )
        rows = []
        for slot in self.slots:
            idle = time.time() - slot.last_activity
            state = slot.status.upper()
            tail = slot.current_match or slot.last_line
            if len(tail) > 70:
                tail = tail[:67] + "..."
            rows.append(
                f"  w{slot.index:<2} {state:<8} matches={slot.matches:<5} "
                f"idle={idle:5.0f}s  {tail}"
            )
        self.console.dashboard(header, rows)

    # ------------------------------------------------------------------ main

    def run(self) -> int:
        self.install_signal_handlers()
        self._prepare_log_dir()

        self.console.system(f"Starting {len(self.slots)} worker(s) against {self.config.api_url}")
        self.console.info("  " + " ".join(self.config.child_argv()) + "  (secrets via environment)")

        for slot in self.slots:
            if self._stop_requested.is_set():
                break
            self._spawn(slot)

        exit_code = 0
        while True:
            if self._stop_requested.is_set():
                self._shutdown(self.config.shutdown_timeout)
                break

            if self.config.quiet and time.time() - self._dashboard_tick >= 2.0:
                self._dashboard_tick = time.time()
                self._render_dashboard()

            for slot in self.slots:
                if slot.process is None:
                    continue
                code = slot.process.poll()
                if code is None:
                    continue
                if slot.status in ("exited", "stopped"):
                    continue
                self._on_exit(slot, code)

            time.sleep(0.15)

        if self._fatal_reason:
            exit_code = 3

        self.console.close_dashboard()
        self._print_summary()
        self._report_orphans()
        return exit_code

    def _on_exit(self, slot: WorkerSlot, code: int) -> None:
        slot.exit_code = code
        slot.status = "exited"
        if slot.reader is not None:
            slot.reader.join(timeout=2.0)
        if slot.log_file is not None:
            try:
                slot.log_file.close()
            except Exception:
                pass
            slot.log_file = None

        lived = time.time() - slot.started_at
        if lived >= HEALTHY_RUN_SECONDS:
            slot.restarts = 0

        if self._stop_requested.is_set() or not self.config.restart:
            return

        if code == 0:
            self.console.info(f"[w{slot.index}] exited cleanly")
            return

        slot.restarts += 1
        delay = min(BACKOFF_MAX, BACKOFF_START * (2 ** (slot.restarts - 1)))
        slot.status = "backoff"
        self.console.warn(
            f"[w{slot.index}] exited with code {code} after {human_duration(lived)} - "
            f"restarting in {delay:.0f}s (restart #{slot.restarts})"
        )

        # Sleep in slices so a Ctrl-C is noticed immediately.
        deadline = time.time() + delay
        while time.time() < deadline:
            if self._stop_requested.is_set():
                return
            time.sleep(0.15)

        if not self._stop_requested.is_set():
            self.console.system(f"restarting worker {slot.index}")
            self._spawn(slot)

    # ---------------------------------------------------------------- summary

    def _print_summary(self) -> None:
        elapsed = time.time() - self.started_at
        total_matches = sum(slot.matches for slot in self.slots)
        total_errors = sum(slot.errors for slot in self.slots)

        lines = ["", "Farm summary", "-" * 62]
        for slot in self.slots:
            state = slot.status if slot.status in ("stopped", "exited") else "running"
            exit_note = "" if slot.exit_code in (None, 0) else f" exit={slot.exit_code}"
            lines.append(
                f"  w{slot.index:<2} matches={slot.matches:<6} claims={slot.claims:<6} "
                f"errors={slot.errors:<4} restarts={slot.restarts:<3} {state}{exit_note}"
            )
        lines.append("-" * 62)
        rate = (total_matches / elapsed * 60.0) if elapsed > 0 else 0.0
        lines.append(
            f"  {total_matches} match(es) submitted in {human_duration(elapsed)} "
            f"({rate:.1f}/min), {total_errors} error line(s)"
        )
        if self._run_dir is not None:
            lines.append(f"  logs: {self._run_dir.resolve()}")
        self.console.block(lines)

        if self._fatal_reason:
            self.console.fail(f"stopped early: {self._fatal_reason}")
            self.console.info(
                "  Fix the token and start again: 'nashium farm --save-config' keeps it for next time."
            )

    def _report_orphans(self) -> None:
        orphans = preflight.find_orphan_containers()
        if not orphans:
            return
        self.console.warn(
            f"{len(orphans)} runner container(s) left behind - clean up with 'nashium farm --clean-orphans'"
        )
        if self.config.clean_orphans:
            preflight.remove_orphan_containers(self.console)
