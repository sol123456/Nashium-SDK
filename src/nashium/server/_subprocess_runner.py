#!/usr/bin/env python3
"""Ultra-fast runner with Unix socket + binary protocol."""
from __future__ import annotations

import json
import socket
import struct
import sys
import time
import traceback
import types
from dataclasses import dataclass

# Constants matching executor
CMD_MOVE = 0
CMD_QUIT = 1
MOVE_NONE = 255

STATUS_OK = 0
STATUS_ERROR = 1


@dataclass(frozen=True, slots=True)
class RoundState:
    round_index: int
    my_history: tuple[int, ...]
    opponent_history: tuple[int, ...]


# Fake module setup
sys.modules['nashium'] = m = types.ModuleType('nashium')
sys.modules['nashium.core'] = mc = types.ModuleType('nashium.core')
m.RoundState = mc.RoundState = RoundState
m.core = mc


class Runner:
    __slots__ = ('bot', 'my', 'opp', 'idx')

    def __init__(self, bot):
        self.bot = bot
        self.my: list[int] = []
        self.opp: list[int] = []
        self.idx = 0

    def move(self, opp_last: int | None) -> tuple[int, float]:
        if opp_last is not None:
            self.opp.append(opp_last)

        state = RoundState(self.idx, tuple(self.my), tuple(self.opp))

        t0 = time.perf_counter()
        mv = int(self.bot.move(state))
        elapsed = time.perf_counter() - t0

        self.my.append(mv)
        self.idx += 1
        return mv, elapsed


def load_bot(code: str, seed: int | None = None):
    ns = {"__name__": "__bot__", "RoundState": RoundState}
    exec(compile(code, "<bot>", "exec"), ns)

    cls = ns.get("Bot")
    if cls is None:
        for v in ns.values():
            if isinstance(v, type) and callable(getattr(v, "move", None)):
                cls = v
                break

    if cls is None:
        raise ValueError("No Bot class found")

    if seed is not None:
        try:
            return cls(seed=seed)
        except TypeError:
            try:
                return cls(seed)
            except TypeError:
                pass
    return cls()


def recvall(sock: socket.socket, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data.extend(chunk)
    return bytes(data)


def send_response(sock: socket.socket, status: int, move: int, elapsed: float):
    """Send binary response: status(1) + move(1) + time(8)"""
    sock.sendall(bytes([status, move]) + struct.pack(">d", elapsed))


def main():
    if len(sys.argv) < 2:
        print("Usage: _subprocess_runner.py <socket_path>", file=sys.stderr)
        sys.exit(1)

    sock_path = sys.argv[1]

    # Connect to host
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # With:
    for attempt in range(10):
        try:
            sock.connect(sock_path)
            break
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.1)
    else:
        print(f"Failed to connect to {sock_path}", file=sys.stderr)
        sys.exit(1)
    # sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    runner: Runner | None = None

    try:
        while True:
            # First, check for load command (length-prefixed JSON)
            if runner is None:
                raw_len = recvall(sock, 4)
                length = struct.unpack(">I", raw_len)[0]
                data = recvall(sock, length)
                cmd = json.loads(data)

                if cmd.get("cmd") == "load":
                    try:
                        bot = load_bot(cmd["code"], cmd.get("seed"))
                        runner = Runner(bot)
                        resp = json.dumps({"status": "ok"}).encode()
                    except Exception as e:
                        tb = traceback.format_exc()
                        resp = json.dumps({
                            "status": "error",
                            "error": f"{type(e).__name__}: {e}\n{tb}"
                        }).encode()

                    sock.sendall(struct.pack(">I", len(resp)) + resp)
                continue

            # Binary protocol for moves: cmd(1) + opp_move(1)
            header = recvall(sock, 2)
            cmd_byte, opp_byte = header[0], header[1]

            if cmd_byte == CMD_QUIT:
                break

            if cmd_byte == CMD_MOVE:
                opp_last = None if opp_byte == MOVE_NONE else opp_byte
                try:
                    mv, elapsed = runner.move(opp_last)
                    send_response(sock, STATUS_OK, mv, elapsed)
                except Exception as e:
                    send_response(sock, STATUS_ERROR, 0, 0.0)

    except ConnectionError:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()