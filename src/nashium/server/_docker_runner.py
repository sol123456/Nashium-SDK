#!/usr/bin/env python3
"""
In-container bot runner. Docker-only.

Wire protocol v2, over a Unix socket:

  load   host -> ctr : u32be len + JSON {"cmd":"load","protocol":2,"code":..,"seed":..}
         ctr -> host : u32be len + JSON {"status":"ok","protocol":2}
                                   or   {"status":"error","protocol":2,"error":..}

  move   host -> ctr : 2 bytes  [CMD_MOVE, opp_last | MOVE_NONE]
         ctr -> host : 26 bytes [status:u8, move:u8, cpu:f64, wall:f64, cgroup:f64]
                       cgroup is -1.0 when not sampled this round.

  quit   host -> ctr : 2 bytes  [CMD_QUIT, 0]

The container never decides policy. It reports what happened; the host decides
whether that constitutes a fault.
"""
from __future__ import annotations

import inspect
import json
import socket
import struct
import sys
import time
import traceback
import types
from dataclasses import dataclass

PROTOCOL_VERSION = 2

CMD_MOVE = 0
CMD_QUIT = 1
MOVE_NONE = 255
STATUS_OK = 0
STATUS_ERROR = 1

# status, move, cpu_seconds, wall_seconds, cgroup_total_seconds
RESPONSE = struct.Struct(">BBddd")

# Sample the cgroup CPU counter every N moves. process_time() only covers this
# process's threads, so a bot spawning children could otherwise hide CPU. The
# cgroup counter covers the whole container. Sampling keeps the cost near zero.
CGROUP_SAMPLE_EVERY = 64

MAX_LOAD_MESSAGE = 8 * 1024 * 1024  # refuse absurd load payloads


@dataclass(frozen=True)
class RoundState:
    round_index: int
    my_history: tuple[int, ...]
    opponent_history: tuple[int, ...]


# Minimal fake `nashium` module so bots can `from nashium import RoundState`.
_m = types.ModuleType("nashium")
_mc = types.ModuleType("nashium.core")
_m.RoundState = _mc.RoundState = RoundState
_m.core = _mc
sys.modules["nashium"] = _m
sys.modules["nashium.core"] = _mc


class BotError(Exception):
    """A bot-authored fault: bad return value, or an exception inside move()."""


# --------------------------------------------------------------- cgroup CPU

class _CgroupCpu:
    """Reads total CPU-seconds for the whole container. Best-effort."""

    def __init__(self) -> None:
        self._fh = None
        self._scale = 1.0
        self._key = b""
        for path, key, scale in (
            ("/sys/fs/cgroup/cpu.stat", b"usage_usec", 1e-6),          # cgroup v2
            ("/sys/fs/cgroup/cpuacct/cpuacct.usage", b"", 1e-9),        # cgroup v1
            ("/sys/fs/cgroup/cpu/cpuacct.usage", b"", 1e-9),            # cgroup v1
        ):
            try:
                self._fh = open(path, "rb")
                self._key = key
                self._scale = scale
                if self.read() is None:
                    raise OSError("unreadable")
                return
            except OSError:
                if self._fh is not None:
                    try:
                        self._fh.close()
                    except OSError:
                        pass
                self._fh = None

    def read(self) -> float | None:
        if self._fh is None:
            return None
        try:
            self._fh.seek(0)
            data = self._fh.read()
            if not self._key:
                return int(data.split()[0]) * self._scale
            for line in data.splitlines():
                if line.startswith(self._key):
                    return int(line.split()[1]) * self._scale
        except (OSError, ValueError, IndexError):
            return None
        return None


# ------------------------------------------------------------------- runner

class BotRunner:
    __slots__ = ("bot", "my_history", "opponent_history", "round_index",
                 "_cgroup", "_cgroup_baseline")

    def __init__(self, bot, cgroup: _CgroupCpu):
        self.bot = bot
        self.my_history: list[int] = []
        self.opponent_history: list[int] = []
        self.round_index = 0
        self._cgroup = cgroup
        # Baseline AFTER load, so module-import CPU is not billed to move().
        self._cgroup_baseline = cgroup.read()

    def make_move(self, opponent_last_move: int | None):
        if opponent_last_move is not None:
            self.opponent_history.append(opponent_last_move)

        state = RoundState(
            round_index=self.round_index,
            my_history=tuple(self.my_history),
            opponent_history=tuple(self.opponent_history),
        )

        cpu0 = time.process_time()
        wall0 = time.perf_counter()
        raw = self.bot.move(state)
        cpu = time.process_time() - cpu0
        wall = time.perf_counter() - wall0

        cgroup_total = -1.0
        if self.round_index % CGROUP_SAMPLE_EVERY == 0 and self._cgroup_baseline is not None:
            now = self._cgroup.read()
            if now is not None:
                cgroup_total = max(0.0, now - self._cgroup_baseline)

        try:
            move = int(raw)
        except (TypeError, ValueError):
            raise BotError(f"move() returned a non-integer value: {raw!r}") from None
        if move not in (0, 1):
            raise BotError(f"move() must return 0 or 1, got {move!r}")

        self.my_history.append(move)
        self.round_index += 1
        return move, cpu, wall, cgroup_total


def _construct(cls, seed):
    """Instantiate the bot, passing the seed only if the constructor can take it."""
    if seed is None:
        return cls()
    try:
        params = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        # Unintrospectable (C extension, exotic metaclass). Mirror the old
        # try/except behaviour so these bots keep receiving their seed.
        try:
            return cls(seed=seed)
        except TypeError:
            try:
                return cls(seed)
            except TypeError:
                return cls()

    values = list(params.values())
    if "seed" in params or any(p.kind is p.VAR_KEYWORD for p in values):
        return cls(seed=seed)
    # *args must accept a positional seed - this is the R7 regression fix.
    if any(
        p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
        for p in values
    ):
        return cls(seed)
    return cls()


def load_bot(code: str, seed: int | None = None):
    ns = {"__name__": "__bot__", "RoundState": RoundState}
    exec(compile(code, "<bot>", "exec"), ns)

    # 1. Prefer create_bot(seed) to match the frontend bot templates exactly
    if "create_bot" in ns and callable(ns["create_bot"]):
        bot = ns["create_bot"](seed)
        if not callable(getattr(bot, "move", None)):
            raise ValueError("create_bot() must return an object with a move(state) method.")
        return bot

    # 2. Fallback to finding a class named 'Bot' (or any class with a move() method)
    cls = ns.get("Bot")
    if not isinstance(cls, type):
        cls = None
    if cls is None:
        for obj in ns.values():
            if (isinstance(obj, type) and obj is not RoundState
                    and callable(getattr(obj, "move", None))):
                cls = obj
                break
    if cls is None:
        raise ValueError(
            "No Bot class found. Define a 'create_bot(seed)' function or a class named 'Bot' with a 'move(state)' method."
        )

    bot = _construct(cls, seed)
    if not callable(getattr(bot, "move", None)):
        raise ValueError("Bot instance has no callable move(state) method")
    return bot


# ---------------------------------------------------------------------- IPC

def recvall(sock: socket.socket, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed by host")
        data.extend(chunk)
    return bytes(data)


def send_json(sock: socket.socket, obj: dict) -> None:
    payload = json.dumps(obj).encode()
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def run(sock_path: str) -> int:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    for _ in range(150):  # up to ~15s
        try:
            sock.connect(sock_path)
            break
        except (FileNotFoundError, ConnectionRefusedError, PermissionError):
            time.sleep(0.1)
    else:
        print(f"Failed to connect to {sock_path}", file=sys.stderr)
        return 1

    try:
        length = struct.unpack(">I", recvall(sock, 4))[0]
        if length > MAX_LOAD_MESSAGE:
            return 1
        cmd = json.loads(recvall(sock, length))
        if cmd.get("cmd") != "load":
            return 1

        cgroup = _CgroupCpu()
        try:
            runner = BotRunner(load_bot(cmd["code"], cmd.get("seed")), cgroup)
        except BaseException as e:  # noqa: BLE001 - SystemExit from bot code included
            send_json(sock, {
                "status": "error",
                "protocol": PROTOCOL_VERSION,
                "error": f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=20)}",
            })
            return 0

        send_json(sock, {"status": "ok", "protocol": PROTOCOL_VERSION})

        while True:
            header = recvall(sock, 2)
            if header[0] == CMD_QUIT:
                break
            if header[0] != CMD_MOVE:
                continue

            opp = None if header[1] == MOVE_NONE else header[1]
            try:
                mv, cpu, wall, cg = runner.make_move(opp)
                sock.sendall(RESPONSE.pack(STATUS_OK, mv, cpu, wall, cg))
            except BaseException:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                sock.sendall(RESPONSE.pack(STATUS_ERROR, 0, 0.0, 0.0, -1.0))
                return 0

    except (ConnectionError, OSError, ValueError, struct.error):
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Error: socket path argument required", file=sys.stderr)
        return 1
    return run(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())