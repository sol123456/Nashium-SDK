"""Multi-worker supervisor with per-worker tabs, 120s safe drain, and /kill switch.

Runs entirely on the Python standard library so both `scripts/bootstrap.py`
and `nashium-worker --workers N` can launch it without extra UI dependencies.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
import glob
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

LABEL_INSTANCE = "com.nashium.worker-instance"

_RE_CLAIMED = re.compile(r"SubMatch\s+(\d+)\s+claimed")
_RE_SUBMITTED = re.compile(r"SubMatch\s+(\d+)\s+submitted")
_RE_BLOCKED = re.compile(r"BLOCKED on SubMatch\s+(\d+)")


@dataclass
class WorkerSlot:
    index: int
    name: str
    instance_id: str
    proc: subprocess.Popen | None = None
    pid: int | None = None
    state: str = "queued"
    current_submatch: str | None = None
    completed: int = 0
    return_code: int | None = None
    force_killed: bool = False
    # Set when the worker's own output shows it gave up on a fatal problem
    # (revoked token, Python traceback). Such a worker can still exit 0, and
    # the final overview must not report that as a clean stop.
    fatal_error: str | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=5000))
    scroll_from_bottom: int = 0  # 0 = follow live tail

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def short_badge(self) -> str:
        if self.state == "running" and self.current_submatch:
            return f"*{self.current_submatch}"
        if self.state == "draining":
            if self.current_submatch:
                return f"~{self.current_submatch}"
            return "~drain"
        if self.state == "blocked":
            return "!blk"
        if self.state == "booting":
            return "..boot"
        if self.state == "queued":
            return "..wait"
        if self.state == "idle":
            return "idle"
        if self.state == "killed":
            return "killed"
        if self.state == "stopped":
            return "!err" if self.fatal_error else "done"
        if self.state == "exited":
            return f"rc={self.return_code}"
        return self.state[:6]


def _slot_fault(slot: WorkerSlot) -> str:
    """Short human description of why a finished slot counts as a failure."""
    rc = "n/a" if slot.return_code is None else str(slot.return_code)
    if slot.fatal_error and slot.return_code in (0, None):
        return f"{slot.name} {slot.fatal_error} (exit {rc})"
    if slot.fatal_error:
        return f"{slot.name} rc={rc} ({slot.fatal_error})"
    return f"{slot.name} rc={rc}"


class WorkerSupervisor:
    """Spawns and manages N `nashium-worker` child processes."""

    def __init__(
        self,
        *,
        worker_cmd: list[str],
        count: int,
        cwd: Path,
        api_url: str = "https://nashium.com",
        shutdown_timeout: float = 120.0,
        stagger_seconds: float = 0.4,
        echo_to_stdout: bool = False,
        auto_exit: bool = False,
    ) -> None:
        self.worker_cmd = list(worker_cmd)
        self.count = max(1, count)
        self.cwd = cwd
        self.api_url = api_url
        self.shutdown_timeout = max(1.0, shutdown_timeout)
        self.stagger_seconds = max(0.0, stagger_seconds)
        self.echo_to_stdout = echo_to_stdout
        # False (default): once every worker has stopped the overview is frozen
        # on screen and stays there until the operator closes it, so crashes and
        # error output can still be reviewed. True: legacy behaviour, the window
        # closes by itself as soon as the last worker exits.
        self.auto_exit = auto_exit

        self.started_at = time.monotonic()
        self.lock = threading.Lock()
        self.slots: list[WorkerSlot] = [
            WorkerSlot(
                index=i,
                name=f"W{i}",
                instance_id=f"w{i}-{uuid.uuid4().hex[:12]}",
            )
            for i in range(1, self.count + 1)
        ]
        self.all_logs: deque[str] = deque(maxlen=15000)
        self.all_scroll_from_bottom: int = 0

        self.shutting_down = False
        self.shutdown_started_at: float | None = None
        self.shutdown_deadline: float | None = None
        self.force_killed = False
        self.force_kill_reason: str | None = None
        self.reaped_containers: int = 0
        self.flash_message: str = ""
        self.flash_until: float = 0.0

        self._stop_launch = threading.Event()
        self._launcher_done = False
        self._threads: list[threading.Thread] = []

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        t = threading.Thread(target=self._launch_loop, name="worker-launcher", daemon=True)
        t.start()
        self._threads.append(t)

    def _launch_loop(self) -> None:
        try:
            for idx, slot in enumerate(self.slots):
                if self._stop_launch.is_set():
                    with self.lock:
                        if slot.proc is None:
                            slot.state = "stopped"
                            slot.return_code = 0
                    continue
                if idx > 0 and self.stagger_seconds > 0:
                    if self._stop_launch.wait(self.stagger_seconds):
                        with self.lock:
                            if slot.proc is None:
                                slot.state = "stopped"
                                slot.return_code = 0
                        continue
                self._spawn_slot(slot)
        finally:
            with self.lock:
                self._launcher_done = True

    def _spawn_slot(self, slot: WorkerSlot) -> None:
        if self._stop_launch.is_set():
            return
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["NASHIUM_WORKER_NAME"] = slot.name
        env["NASHIUM_WORKER_INSTANCE_ID"] = slot.instance_id

        cmd = [*self.worker_cmd, "--name", slot.name]
        popen_kwargs: dict = {
            "cwd": str(self.cwd),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
            "errors": "replace",
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as e:  # noqa: BLE001
            self._record_line(slot, f"[supervisor] Failed to start {slot.name}: {e}")
            with self.lock:
                slot.state = "exited"
                slot.return_code = 1
            return

        with self.lock:
            slot.proc = proc
            slot.pid = proc.pid
            slot.state = "booting"

        reader = threading.Thread(
            target=self._read_worker_output,
            args=(slot, proc),
            name=f"reader-{slot.name}",
            daemon=True,
        )
        reader.start()
        self._threads.append(reader)

    def _read_worker_output(self, slot: WorkerSlot, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                self._update_state_from_line(slot, line)
                self._record_line(slot, line)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            rc = proc.wait()
            with self.lock:
                slot.return_code = rc
                slot.current_submatch = None
                if slot.force_killed:
                    slot.state = "killed"
                elif rc == 0 or self.shutting_down:
                    slot.state = "stopped"
                else:
                    slot.state = "exited"
            self._record_line(
                slot,
                f"[supervisor] {slot.name} exited (code={rc}"
                f"{f', {slot.fatal_error}' if slot.fatal_error else ''}"
                f"{', force-killed' if slot.force_killed else ''})",
            )

    def _record_line(self, slot: WorkerSlot, line: str) -> None:
        with self.lock:
            was_scrolled = slot.scroll_from_bottom > 0
            slot.logs.append(line)
            if was_scrolled:
                slot.scroll_from_bottom = min(len(slot.logs) - 1, slot.scroll_from_bottom + 1)

            all_line = line if f"[{slot.name}]" in line else f"[{slot.name}] {line}"
            all_scrolled = self.all_scroll_from_bottom > 0
            self.all_logs.append(all_line)
            if all_scrolled:
                self.all_scroll_from_bottom = min(
                    len(self.all_logs) - 1, self.all_scroll_from_bottom + 1
                )

        if self.echo_to_stdout:
            out = line if f"[{slot.name}]" in line else f"[{slot.name}] {line}"
            print(out, flush=True)

    def _update_state_from_line(self, slot: WorkerSlot, line: str) -> None:
        lowered = line.lower()
        with self.lock:
            if slot.fatal_error is None:
                # A worker that gives up on a fatal problem can still exit 0
                # (an auth failure just breaks its loop), so remember it here -
                # otherwise the final overview would report a clean session.
                if "authentication failed" in lowered or "token is wrong or revoked" in lowered:
                    slot.fatal_error = "auth failure"
                elif "traceback (most recent call last)" in lowered:
                    slot.fatal_error = "python traceback"

            if "Docker preflight OK" in line:
                if slot.state == "booting":
                    slot.state = "draining" if self.shutting_down else "idle"
                return

            m = _RE_CLAIMED.search(line)
            if m:
                slot.current_submatch = f"#{m.group(1)}"
                slot.state = "draining" if self.shutting_down else "running"
                return

            m = _RE_SUBMITTED.search(line)
            if m:
                slot.completed += 1
                slot.current_submatch = None
                slot.state = "draining" if self.shutting_down else "idle"
                return

            if _RE_BLOCKED.search(line) or "Docker preflight failed" in line or "Server unreachable" in line:
                slot.state = "blocked"
                return

            if "finishing current work then stopping" in line:
                slot.state = "draining"
                return

    # -------------------------------------------------------------- shutdown

    def set_flash(self, message: str, duration: float = 4.0) -> None:
        with self.lock:
            self.flash_message = message
            self.flash_until = time.monotonic() + duration

    def alive_slots(self) -> list[WorkerSlot]:
        with self.lock:
            return [s for s in self.slots if s.alive]

    def all_finished(self) -> bool:
        with self.lock:
            if not self._launcher_done and not self.force_killed:
                return False
            return all(not s.alive for s in self.slots)

    def abnormal_slots(self) -> list[WorkerSlot]:
        """Slots that ended badly: crashed, failed to start, or were force-killed.

        Workers stopped by an operator (SIGTERM/SIGINT drain) are *not* abnormal,
        which keeps the final banner honest after a deliberate Ctrl+C.
        """
        shutdown_signals = {0, -2, -15, 130, 143}
        with self.lock:
            out: list[WorkerSlot] = []
            for s in self.slots:
                if s.state in ("exited", "killed") or s.fatal_error:
                    out.append(s)
                elif s.state == "stopped" and (s.return_code or 0) not in shutdown_signals:
                    out.append(s)
            return out

    def hold_banner(self) -> tuple[str, bool]:
        """One-line summary shown while the finished overview is held on screen.

        Returns (text, had_failures).
        """
        bad = self.abnormal_slots()
        with self.lock:
            total_done = sum(s.completed for s in self.slots)
            uptime = _format_uptime(time.monotonic() - self.started_at)
            reason = self.force_kill_reason
        tail = f"{total_done} match(es) submitted in {uptime}"
        if self.force_killed:
            head = f"WORKERS FORCE-KILLED ({reason or 'unknown reason'})"
        elif bad:
            detail = ", ".join(_slot_fault(s) for s in bad)
            head = f"SESSION ENDED WITH ERRORS: {len(bad)}/{self.count} worker(s) failed ({detail})"
        else:
            head = f"SESSION ENDED: all {self.count} worker(s) stopped cleanly"
        return f" {head} | {tail} ", bool(bad) or self.force_killed

    def seconds_until_force_kill(self) -> float:
        with self.lock:
            if self.shutdown_deadline is None:
                return self.shutdown_timeout
            return max(0.0, self.shutdown_deadline - time.monotonic())

    def begin_graceful_shutdown(self) -> bool:
        """Send SIGTERM to all workers and start the 120s safety timer.

        Returns True if shutdown was newly initiated, False if already shutting down.
        """
        self._stop_launch.set()
        with self.lock:
            if self.shutting_down:
                rem = max(0.0, (self.shutdown_deadline or 0.0) - time.monotonic())
                self.flash_message = (
                    f"Already shutting down safely ({rem:.0f}s left). "
                    f"Type /kill + Enter to murder immediately."
                )
                self.flash_until = time.monotonic() + 5.0
                return False

            self.shutting_down = True
            now = time.monotonic()
            self.shutdown_started_at = now
            self.shutdown_deadline = now + self.shutdown_timeout
            targets = [s for s in self.slots if s.alive]
            for s in targets:
                s.state = "draining"

        for s in targets:
            self._terminate_slot(s)
        return True

    def _terminate_slot(self, slot: WorkerSlot) -> None:
        proc = slot.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except ProcessLookupError:
            pass
        except Exception:  # noqa: BLE001
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def force_kill_all(self, *, reason: str = "user /kill") -> int:
        """Immediately SIGKILL all live workers and reap their Docker containers."""
        self._stop_launch.set()
        with self.lock:
            if not self.shutting_down:
                self.shutting_down = True
                self.shutdown_started_at = time.monotonic()
                self.shutdown_deadline = time.monotonic()
            self.force_killed = True
            self.force_kill_reason = reason
            targets = [s for s in self.slots if s.alive]
            for s in targets:
                s.force_killed = True
                s.state = "killed"

        for s in targets:
            proc = s.proc
            if proc is None or proc.poll() is not None:
                continue
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

        for s in targets:
            if s.proc is not None:
                try:
                    s.proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass

        killed_ids = [s.instance_id for s in targets]
        reaped = _reap_containers_for_instances(killed_ids)
        with self.lock:
            self.reaped_containers += reaped
        return len(targets)


def _reap_containers_for_instances(instance_ids: list[str]) -> int:
    """Remove any lingering Docker bot containers belonging to force-killed workers."""
    if not instance_ids or not shutil.which("docker"):
        return 0
    to_remove: list[str] = []
    for inst_id in instance_ids:
        try:
            res = subprocess.run(
                ["docker", "ps", "-aq", "--filter", f"label={LABEL_INSTANCE}={inst_id}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for cid in res.stdout.splitlines():
                cid = cid.strip()
                if cid and cid not in to_remove:
                    to_remove.append(cid)
        except Exception:  # noqa: BLE001
            pass

    if not to_remove:
        return 0
    try:
        subprocess.run(
            ["docker", "rm", "-f", *to_remove],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return len(to_remove)
    except Exception:  # noqa: BLE001
        return 0


# ============================================================ curses TUI mode

def _wrap_lines(lines: list[str], width: int) -> list[str]:
    if width <= 4:
        return lines
    wrapped: list[str] = []
    for ln in lines:
        if not ln:
            wrapped.append("")
            continue
        if len(ln) <= width:
            wrapped.append(ln)
        else:
            for start in range(0, len(ln), width):
                wrapped.append(ln[start : start + width])
    return wrapped


def _format_uptime(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _run_curses_tui(sup: WorkerSupervisor) -> None:
    import curses

    sig_triggered = threading.Event()

    def _on_signal(_signum, _frame):
        sig_triggered.set()

    prev_int = signal.signal(signal.SIGINT, _on_signal)
    prev_term = signal.signal(signal.SIGTERM, _on_signal)

    try:
        curses.wrapper(lambda stdscr: _curses_loop(stdscr, sup, sig_triggered))
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def _curses_loop(stdscr, sup: WorkerSupervisor, sig_triggered: threading.Event) -> None:
    import curses

    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.nodelay(False)
    stdscr.timeout(50)
    stdscr.keypad(True)

    has_colors = False
    if curses.has_colors():
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)    # selected tab
            curses.init_pair(2, curses.COLOR_GREEN, -1)                   # running / ok
            curses.init_pair(3, curses.COLOR_YELLOW, -1)                  # warning / drain
            curses.init_pair(4, curses.COLOR_RED, -1)                     # error / blocked
            curses.init_pair(5, curses.COLOR_CYAN, -1)                    # header accent
            curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # shutdown banner
            curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_RED)     # urgent banner
            has_colors = True
        except curses.error:
            has_colors = False

    def cpair(n: int, extra: int = 0) -> int:
        return (curses.color_pair(n) | extra) if has_colors else extra

    def safe_put(y: int, x: int, text: str, attr: int = 0) -> None:
        h, w = stdscr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w - 1:
            return
        avail = (w - 1) - x
        if avail <= 0:
            return
        try:
            stdscr.addnstr(y, x, text, avail, attr)
        except curses.error:
            pass

    def fill_row(y: int, text: str, attr: int = 0) -> None:
        h, w = stdscr.getmaxyx()
        if y < 0 or y >= h or w <= 1:
            return
        padded = text[: w - 1].ljust(w - 1)
        safe_put(y, 0, padded, attr)

    sup.start()

    # selected_tab: 1..N for individual worker tabs, 0 for combined "All" tab
    selected_tab = 1
    cmd_mode = False
    cmd_buf = ""

    # ------------------------------------------------------- session-end hold
    # When the last worker stops - crash, auth failure, drain, /kill - the
    # overview is NOT torn down any more. It freezes on screen with every
    # worker's final state and its full log history, so whatever happened can
    # still be read, and it only closes when the operator dismisses it.
    # `--auto-exit` restores the old "window vanishes with the last worker"
    # behaviour for scripts and unattended runs.
    CLOSE_KEYS = frozenset({3, 4, 27, ord("q"), ord("Q")})  # Ctrl+C, Ctrl+D, Esc, q
    session_held = False
    hold_focus_done = False

    def _close_requested(ch: int) -> bool:
        """True only when the operator *deliberately* dismissed the overview."""
        if ch not in CLOSE_KEYS:
            return False
        if ch != 27:
            return True
        # A bare ESC is ambiguous: either a genuine Esc press, or the first byte
        # of an escape sequence this terminal's terminfo does not map (arrow keys
        # sent as CSI sequences while not in application-cursor mode, for
        # instance). ncurses hands back the ESC and leaves the tail queued, so
        # peek behind it: only close when nothing follows. Reviewing the logs
        # must never be able to slam the window shut by accident.
        stdscr.timeout(30)
        nxt = stdscr.getch()
        stdscr.timeout(50)
        if nxt == -1:
            return True
        stdscr.nodelay(True)
        try:
            while stdscr.getch() != -1:
                pass
        except curses.error:
            pass
        finally:
            stdscr.nodelay(False)
            stdscr.timeout(50)
        return False

    def _enter_hold() -> None:
        """Freeze the overview and focus the first worker that died badly."""
        nonlocal session_held, cmd_mode, cmd_buf, selected_tab, hold_focus_done
        session_held = True
        cmd_mode = False
        cmd_buf = ""
        if hold_focus_done:
            return
        hold_focus_done = True
        bad = sup.abnormal_slots()
        if not bad:
            return
        with sup.lock:
            current = (
                sup.slots[selected_tab - 1] if 1 <= selected_tab <= sup.count else None
            )
        if current is not None and current.state in ("exited", "killed"):
            return  # already looking at a failed worker
        selected_tab = bad[0].index
        sup.set_flash(
            f"Showing {bad[0].name} - it stopped with an error. "
            f"Press q or Ctrl+C to close this overview.",
            duration=8.0,
        )

    while True:
        if sig_triggered.is_set():
            sig_triggered.clear()
            if session_held:
                break  # Ctrl+C on a finished overview simply closes the window
            if sup.begin_graceful_shutdown():
                cmd_buf = ""
            cmd_mode = True

        finished_now = False
        if sup.shutting_down:
            if sup.all_finished():
                finished_now = True
            elif sup.seconds_until_force_kill() <= 0:
                sup.force_kill_all(reason=f"{int(sup.shutdown_timeout)}s safety timeout")
                finished_now = True
        elif sup.all_finished():
            finished_now = True

        if finished_now and not session_held:
            if sup.auto_exit:
                break
            _enter_hold()

        # ------------------------------ render
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 6 or width < 30:
            safe_put(
                0,
                0,
                "Session ended - press q or Ctrl+C to close"
                if session_held
                else "Terminal too small - enlarge window",
                curses.A_BOLD,
            )
            stdscr.refresh()
            ch = stdscr.getch()
            if session_held:
                if _close_requested(ch):
                    break
            elif ch == 3:
                sup.begin_graceful_shutdown()
            continue

        with sup.lock:
            n_running = sum(1 for s in sup.slots if s.state == "running")
            n_idle = sum(1 for s in sup.slots if s.state == "idle")
            n_alive = sum(1 for s in sup.slots if s.alive)
            total_done = sum(s.completed for s in sup.slots)
            uptime = _format_uptime(time.monotonic() - sup.started_at)
            flash = sup.flash_message if time.monotonic() < sup.flash_until else ""

        # Row 0: Header / Shutdown banner / final session summary
        if session_held:
            banner, had_failures = sup.hold_banner()
            fill_row(0, banner, cpair(7 if had_failures else 2, curses.A_BOLD))
        elif sup.shutting_down:
            rem = sup.seconds_until_force_kill()
            banner = (
                f" SAFE SHUTDOWN: waiting for {n_alive}/{sup.count} worker(s) to finish "
                f"current matches | force-kill safety net in {rem:0.0f}s "
            )
            banner_attr = cpair(7, curses.A_BOLD) if rem <= 20 else cpair(6, curses.A_BOLD)
            fill_row(0, banner, banner_attr)
        else:
            left = f" Nashium Workers ({sup.count}) -> {sup.api_url}"
            right = (
                f"active:{n_running}  idle:{n_idle}  "
                f"completed:{total_done}  up {uptime} "
            )
            gap = max(1, (width - 1) - len(left) - len(right))
            fill_row(0, left + (" " * gap) + right, cpair(5, curses.A_BOLD))

        # Row 1: Tab bar (keep selected tab scrolled into view on narrow terminals)
        tab_items: list[tuple[int, str, int]] = []
        with sup.lock:
            for s in sup.slots:
                label = f" {s.index}:{s.name} {s.short_badge()} "
                if selected_tab == s.index:
                    attr = cpair(1, curses.A_BOLD)
                elif s.state == "running":
                    attr = cpair(2, curses.A_BOLD)
                elif s.state in ("draining", "booting"):
                    attr = cpair(3)
                elif s.state in ("blocked", "exited", "killed") or s.fatal_error:
                    attr = cpair(4, curses.A_BOLD)
                else:
                    attr = curses.A_NORMAL
                tab_items.append((s.index, label, attr))

        all_attr = cpair(1, curses.A_BOLD) if selected_tab == 0 else curses.A_DIM
        tab_items.append((0, " 0:All ", all_attr))

        # Horizontal windowing so active tab is always visible
        positions: list[tuple[int, int, str, int]] = []
        cursor_x = 1
        sel_start, sel_end = 0, 0
        for tid, label, attr in tab_items:
            start_x = cursor_x
            end_x = start_x + len(label)
            positions.append((start_x, tid, label, attr))
            if tid == selected_tab:
                sel_start, sel_end = start_x, end_x
            cursor_x = end_x + 1

        scroll_x = 0
        max_x = width - 2
        if sel_end > max_x:
            scroll_x = sel_end - max_x
        if sel_start - scroll_x < 1:
            scroll_x = max(0, sel_start - 1)

        for start_x, _tid, label, attr in positions:
            draw_x = start_x - scroll_x
            if draw_x + len(label) <= 0 or draw_x >= width - 1:
                continue
            if draw_x < 0:
                label = label[-draw_x:]
                draw_x = 0
            safe_put(1, draw_x, label, attr)

        # Row 2: Divider
        fill_row(2, "-" * (width - 1), curses.A_DIM)

        # Rows 3 .. height - 3: Log viewport
        log_top = 3
        log_height = max(1, height - 5)

        with sup.lock:
            if selected_tab == 0:
                raw_lines = list(sup.all_logs)
                scroll_off = sup.all_scroll_from_bottom
            else:
                slot = sup.slots[selected_tab - 1]
                raw_lines = list(slot.logs)
                scroll_off = slot.scroll_from_bottom

        wrapped = _wrap_lines(raw_lines, width - 2)
        total_visual = len(wrapped)
        max_scroll = max(0, total_visual - log_height)
        if scroll_off > max_scroll:
            scroll_off = max_scroll
            with sup.lock:
                if selected_tab == 0:
                    sup.all_scroll_from_bottom = scroll_off
                else:
                    sup.slots[selected_tab - 1].scroll_from_bottom = scroll_off

        end_idx = total_visual - scroll_off
        start_idx = max(0, end_idx - log_height)
        visible = wrapped[start_idx:end_idx]

        if not visible:
            safe_put(
                log_top,
                1,
                "No output was captured from this worker."
                if session_held
                else "Waiting for worker output...",
                curses.A_DIM,
            )
        else:
            for row_offset, ln in enumerate(visible):
                line_attr = curses.A_NORMAL
                if "[ERROR]" in ln or "BLOCKED" in ln or "force-killed" in ln:
                    line_attr = cpair(4)
                elif "[WARNING]" in ln or "fault  |" in ln:
                    line_attr = cpair(3)
                elif ("SubMatch" in ln and ("finished" in ln or "submitted" in ln)) or "preflight OK" in ln:
                    line_attr = cpair(2)
                safe_put(log_top + row_offset, 1, ln, line_attr)

        # Row height - 2: Per-tab status bar (or flash message)
        if flash:
            fill_row(height - 2, f" {flash}", cpair(3, curses.A_BOLD))
        else:
            with sup.lock:
                if selected_tab == 0:
                    follow_txt = (
                        "LIVE"
                        if sup.all_scroll_from_bottom == 0
                        else f"PAUSED (+{sup.all_scroll_from_bottom} lines - End/G for live)"
                    )
                    status_line = (
                        f" All Workers ({n_alive}/{sup.count} alive) | "
                        f"completed: {total_done} | {follow_txt}"
                    )
                else:
                    s = sup.slots[selected_tab - 1]
                    pid_txt = f"PID {s.pid}" if s.pid else "not started"
                    state_txt = (
                        f"{s.state} ({s.current_submatch})"
                        if s.current_submatch
                        else s.state
                    )
                    follow_txt = (
                        "LIVE"
                        if s.scroll_from_bottom == 0
                        else f"PAUSED (+{s.scroll_from_bottom} lines - End/G for live)"
                    )
                    status_line = (
                        f" Tab {s.index}/{sup.count}: {s.name} ({pid_txt}) | "
                        f"state: {state_txt} | matches done: {s.completed} | {follow_txt}"
                    )
            fill_row(height - 2, status_line, curses.A_REVERSE)

        # Row height - 1: Help bar OR interactive /command bar
        if session_held:
            fill_row(
                height - 1,
                f" OVERVIEW KEPT OPEN (nothing is running) | q / Ctrl+C / Esc: close window | "
                f"1-{min(9, sup.count)}/Tab: worker tabs | 0: all | "
                f"Up/Dn/PgUp/PgDn/Home/End: review logs ",
                cpair(6, curses.A_BOLD),
            )
        elif sup.shutting_down or cmd_mode:
            if sup.shutting_down:
                prompt = " Type /kill + Enter to instantly murder all workers (Tab/0-9 switches tabs): "
            else:
                prompt = " Command (/stop = safe 120s stop, /kill = instant murder, Esc = cancel): "
            shown = f"{prompt}{cmd_buf}_"
            fill_row(height - 1, shown, cpair(3, curses.A_BOLD) if sup.shutting_down else cpair(5, curses.A_BOLD))
        else:
            help_txt = (
                f" 1-{min(9, sup.count)}/Tab/<-/->: tab | 0: all | "
                f"Up/Dn/PgUp/PgDn: scroll | /: command (/stop, /kill) | Ctrl+C: safe stop ({int(sup.shutdown_timeout)}s)"
            )
            fill_row(height - 1, help_txt, curses.A_DIM)

        stdscr.refresh()

        # ------------------------------ input handling
        ch = stdscr.getch()
        if ch == -1:
            continue
        if ch == curses.KEY_RESIZE:
            continue
        if session_held and _close_requested(ch):
            break  # operator dismissed the finished overview
        if ch == 3:  # Ctrl+C in raw/cbreak mode
            if sup.begin_graceful_shutdown():
                cmd_buf = ""
            cmd_mode = True
            continue

        def adjust_scroll(delta: int, *, to_bottom: bool = False, to_top: bool = False) -> None:
            with sup.lock:
                if selected_tab == 0:
                    if to_bottom:
                        sup.all_scroll_from_bottom = 0
                    elif to_top:
                        sup.all_scroll_from_bottom = max(0, total_visual - log_height)
                    else:
                        sup.all_scroll_from_bottom = max(0, sup.all_scroll_from_bottom + delta)
                else:
                    sl = sup.slots[selected_tab - 1]
                    if to_bottom:
                        sl.scroll_from_bottom = 0
                    elif to_top:
                        sl.scroll_from_bottom = max(0, total_visual - log_height)
                    else:
                        sl.scroll_from_bottom = max(0, sl.scroll_from_bottom + delta)

        def cycle_tab(step: int) -> None:
            nonlocal selected_tab
            order = [*range(1, sup.count + 1), 0]
            idx = order.index(selected_tab) if selected_tab in order else 0
            selected_tab = order[(idx + step) % len(order)]

        # Scrolling & tab keys always work, even during shutdown
        if ch == curses.KEY_UP:
            adjust_scroll(1)
            continue
        if ch == curses.KEY_DOWN:
            adjust_scroll(-1)
            continue
        if ch == curses.KEY_PPAGE:
            adjust_scroll(max(1, log_height // 2))
            continue
        if ch == curses.KEY_NPAGE:
            adjust_scroll(-max(1, log_height // 2))
            continue
        if ch == curses.KEY_HOME:
            adjust_scroll(0, to_top=True)
            continue
        if ch == curses.KEY_END:
            adjust_scroll(0, to_bottom=True)
            continue
        if ch == 9:  # Tab
            cycle_tab(1)
            continue
        if ch == curses.KEY_BTAB:  # Shift+Tab
            cycle_tab(-1)
            continue
        if ch == curses.KEY_LEFT and not cmd_buf:
            cycle_tab(-1)
            continue
        if ch == curses.KEY_RIGHT and not cmd_buf:
            cycle_tab(1)
            continue

        # Session ended: the overview is frozen, so there is nothing left to
        # command. Tab switching and log scrolling stay available for review;
        # every other key (except the close keys handled above) is ignored.
        if session_held:
            if ord("0") <= ch <= ord("9"):
                digit = ch - ord("0")
                if digit == 0 or digit <= sup.count:
                    selected_tab = digit
            elif ch in (ord("a"), ord("A")):
                selected_tab = 0
            elif ch in (ord("g"), ord("G")):
                adjust_scroll(0, to_bottom=True)
            elif ch == ord("["):
                cycle_tab(-1)
            elif ch == ord("]"):
                cycle_tab(1)
            elif ch == ord("/"):
                sup.set_flash(
                    "Nothing is running - press q or Ctrl+C to close this overview."
                )
            continue

        # Command input handling (active after `/` or during shutdown)
        if cmd_mode or sup.shutting_down:
            # Allow 0-9 tab switching when command buffer is empty!
            if not cmd_buf and ord("0") <= ch <= ord("9"):
                digit = ch - ord("0")
                if digit == 0 or digit <= sup.count:
                    selected_tab = digit
                continue

            if ch == 27:  # Esc
                cmd_buf = ""
                if not sup.shutting_down:
                    cmd_mode = False
                continue

            if ch in (curses.KEY_BACKSPACE, 127, 8):
                if cmd_buf:
                    cmd_buf = cmd_buf[:-1]
                elif not sup.shutting_down:
                    cmd_mode = False
                continue

            if ch in (10, 13, curses.KEY_ENTER):
                entered = cmd_buf.strip().lower()
                cmd_buf = ""
                if entered in ("/kill", "kill"):
                    sup.force_kill_all(reason="user /kill command")
                    if sup.auto_exit:
                        break
                    # Keep the frozen overview on screen so the operator can
                    # still read what the workers did before they were killed.
                    cmd_mode = False
                    continue
                if entered in ("/stop", "stop", "/quit", "quit"):
                    sup.begin_graceful_shutdown()
                    cmd_mode = True
                elif entered:
                    sup.set_flash(f"Unknown command '{entered}'. Use /stop or /kill.")
                    if not sup.shutting_down:
                        cmd_mode = False
                elif not sup.shutting_down:
                    cmd_mode = False
                continue

            if 32 <= ch <= 126 and len(cmd_buf) < 40:
                cmd_buf += chr(ch)
            continue

        # Normal navigation mode
        if ch == ord("/"):
            cmd_mode = True
            cmd_buf = "/"
        elif ord("0") <= ch <= ord("9"):
            digit = ch - ord("0")
            if digit == 0 or digit <= sup.count:
                selected_tab = digit
        elif ch in (ord("a"), ord("A")):
            selected_tab = 0
        elif ch in (ord("g"), ord("G")):
            adjust_scroll(0, to_bottom=True)
        elif ch == ord("["):
            cycle_tab(-1)
        elif ch == ord("]"):
            cycle_tab(1)
        elif ch in (ord("q"), ord("Q")):
            sup.begin_graceful_shutdown()
            cmd_mode = True


# ============================================================ plain stream mode

def _run_plain(sup: WorkerSupervisor) -> None:
    sig_event = threading.Event()
    kill_event = threading.Event()

    def _on_signal(_signum, _frame):
        sig_event.set()

    prev_int = signal.signal(signal.SIGINT, _on_signal)
    prev_term = signal.signal(signal.SIGTERM, _on_signal)

    def _stdin_watcher() -> None:
        if sys.stdin is None or sys.stdin.closed:
            return
        while not sup.all_finished():
            try:
                line = sys.stdin.readline()
            except Exception:  # noqa: BLE001
                return
            if not line:
                return
            cmd = line.strip().lower()
            if cmd in ("/kill", "kill"):
                kill_event.set()
                return
            if cmd in ("/stop", "stop", "/quit", "quit"):
                sig_event.set()
            elif cmd.startswith("/"):
                print(
                    f"[supervisor] Unknown command '{cmd}'. Available: /stop, /kill",
                    flush=True,
                )

    watcher = threading.Thread(target=_stdin_watcher, name="stdin-watcher", daemon=True)
    watcher.start()

    try:
        print(
            f"[supervisor] Starting {sup.count} worker(s) -> {sup.api_url} "
            f"(Ctrl+C for safe {int(sup.shutdown_timeout)}s stop, or type /kill + Enter to murder)",
            flush=True,
        )
        sup.start()
        last_report = 0.0

        while not sup.all_finished():
            if kill_event.is_set():
                sup.force_kill_all(reason="user /kill command")
                break

            if sig_event.is_set():
                sig_event.clear()
                started_now = sup.begin_graceful_shutdown()
                rem = sup.seconds_until_force_kill()
                alive = len(sup.alive_slots())
                if started_now:
                    print(
                        f"\n[supervisor] Safe shutdown started: waiting up to "
                        f"{rem:.0f}s for {alive} worker(s) to finish current matches.",
                        flush=True,
                    )
                    print(
                        "[supervisor] Type /kill and press Enter to instantly murder all remaining workers.",
                        flush=True,
                    )
                    last_report = time.monotonic()
                else:
                    print(
                        f"[supervisor] Still shutting down safely ({rem:.0f}s remaining, "
                        f"{alive} worker(s) active). Type /kill + Enter to force-kill now.",
                        flush=True,
                    )

            if sup.shutting_down:
                rem = sup.seconds_until_force_kill()
                if rem <= 0:
                    print(
                        f"[supervisor] {int(sup.shutdown_timeout)}s safety timeout reached — force-killing remaining workers.",
                        flush=True,
                    )
                    sup.force_kill_all(reason=f"{int(sup.shutdown_timeout)}s safety timeout")
                    break
                if time.monotonic() - last_report >= 15.0:
                    alive_names = ", ".join(s.name for s in sup.alive_slots())
                    print(
                        f"[supervisor] Waiting on [{alive_names}] ({rem:.0f}s until force-kill; type /kill + Enter to murder now)...",
                        flush=True,
                    )
                    last_report = time.monotonic()

            time.sleep(0.1)
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


# ============================================================ Windows Terminal

def _find_wt_exe() -> str | None:
    wt = shutil.which("wt.exe") or shutil.which("wt")
    if wt:
        return wt
    for candidate in glob.glob("/mnt/c/Users/*/AppData/Local/Microsoft/WindowsApps/wt.exe"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def launch_windows_terminal_tabs(
    *,
    worker_cmd: list[str],
    count: int,
    cwd: Path,
) -> int:
    """Optional mode (`--wt-tabs`): spawn N native Windows Terminal WSL2 tabs."""
    wt = _find_wt_exe()
    if not wt:
        print(
            "Error: wt.exe (Windows Terminal) was not found on PATH. "
            "Omit --wt-tabs to use the built-in tabbed console.",
            file=sys.stderr,
        )
        return 1

    distro = os.environ.get("WSL_DISTRO_NAME", "")
    wt_args: list[str] = [wt, "-w", "0"]
    for i in range(1, count + 1):
        if i > 1:
            wt_args.append(";")
        name = f"W{i}"
        quoted_cmd = " ".join(shlex.quote(part) for part in [*worker_cmd, "--name", name])
        bash_script = (
            f"cd {shlex.quote(str(cwd))} && "
            f"exec env PYTHONUNBUFFERED=1 NASHIUM_WORKER_NAME={name} {quoted_cmd}"
        )
        tab_cmd = ["new-tab", "--title", f"Nashium {name}", "wsl.exe"]
        if distro:
            tab_cmd.extend(["-d", distro])
        tab_cmd.extend(["--", "bash", "-lc", bash_script])
        wt_args.extend(tab_cmd)

    subprocess.check_call(wt_args)
    print(f"Opened {count} Windows Terminal tab(s).")
    return 0


# ============================================================ public entry

def _can_use_curses() -> bool:
    if os.name == "nt":
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.environ.get("TERM", "").lower() in ("", "dumb"):
        return False
    try:
        import curses  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _print_failure_digest(sup: WorkerSupervisor, *, tail_lines: int = 15) -> None:
    """Re-print the tail of every worker that ended badly.

    The tabbed console now stays open until the operator closes it, but once it
    does close the screen is cleared - so the interesting last lines of each
    crashed / killed worker are echoed to stderr and survive in the terminal
    scrollback for a post-mortem.
    """
    for s in sup.abnormal_slots():
        with sup.lock:
            lines = list(s.logs)[-tail_lines:]
        if not lines:
            continue
        rc = "n/a" if s.return_code is None else str(s.return_code)
        reason = f", {s.fatal_error}" if s.fatal_error else ""
        print(
            f"\n----- {s.name}: last {len(lines)} line(s), exit code {rc}{reason} -----",
            file=sys.stderr,
            flush=True,
        )
        for ln in lines:
            print(f"[{s.name}] {ln}", file=sys.stderr, flush=True)


def run_supervised_workers(
    *,
    worker_cmd: list[str],
    count: int,
    cwd: Path,
    api_url: str = "https://nashium.com",
    shutdown_timeout: float = 120.0,
    ui_mode: str = "auto",
    auto_exit: bool = False,
) -> int:
    """Launch `count` workers in tabbed TUI, plain stream, or native WT tabs.

    `ui_mode`:
      - "auto"    : tabbed TUI when count > 1 and TTY supports curses, else plain
      - "tabs"    : tabbed TUI (falls back to plain if non-TTY)
      - "plain"   : prefixed single-stream output with 120s drain + /kill on stdin
      - "wt-tabs" : spawn separate native Windows Terminal tabs via wt.exe

    `auto_exit`:
      - False (default): the tabbed console stays on screen after the last
        worker stops - including after a crash - until the operator closes it
        with `q`, `Esc` or `Ctrl+C`, so the final states and logs can be read.
      - True: legacy behaviour, the console closes as soon as every worker has
        exited (use this for unattended/scripted runs).
    """
    if ui_mode == "wt-tabs":
        return launch_windows_terminal_tabs(worker_cmd=worker_cmd, count=count, cwd=cwd)

    use_tui = False
    if ui_mode == "tabs":
        use_tui = _can_use_curses()
    elif ui_mode == "auto" and count > 1:
        use_tui = _can_use_curses()

    sup = WorkerSupervisor(
        worker_cmd=worker_cmd,
        count=count,
        cwd=cwd,
        api_url=api_url,
        shutdown_timeout=shutdown_timeout,
        echo_to_stdout=not use_tui,
        auto_exit=auto_exit,
    )

    if use_tui:
        _run_curses_tui(sup)
    else:
        _run_plain(sup)

    # Print clear final exit summary
    total_completed = sum(s.completed for s in sup.slots)
    if sup.force_killed:
        killed_count = sum(1 for s in sup.slots if s.force_killed)
        extra = (
            f" and reaped {sup.reaped_containers} bot container(s)"
            if sup.reaped_containers
            else ""
        )
        print(
            f"Force-killed {killed_count} worker(s) ({sup.force_kill_reason}){extra}. "
            f"Completed matches before exit: {total_completed}.",
            flush=True,
        )
        if use_tui:
            _print_failure_digest(sup)
        return 130

    failed = [s for s in sup.slots if (s.return_code or 0) != 0 and not sup.shutting_down]
    # A worker can also give up on a fatal problem (revoked token, traceback)
    # and still exit 0 - that is an error too, unless the operator stopped it.
    bad: dict[int, WorkerSlot] = {s.index: s for s in failed}
    if not sup.shutting_down:
        for s in sup.slots:
            if s.fatal_error:
                bad.setdefault(s.index, s)
    if bad:
        details = ", ".join(_slot_fault(s) for s in bad.values())
        print(f"Workers exited with errors: {details}", file=sys.stderr, flush=True)
        if use_tui:
            _print_failure_digest(sup)
        return 1

    if sup.shutting_down and sup.shutdown_started_at is not None:
        drained_in = time.monotonic() - sup.shutdown_started_at
        print(
            f"All {sup.count} worker(s) stopped safely in {drained_in:.1f}s "
            f"(completed matches: {total_completed}).",
            flush=True,
        )
    return 0
