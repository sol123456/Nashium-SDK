#!/usr/bin/env python3
"""
Runner script for sandboxed bot execution.
Supports two modes:
  - Stdin/stdout JSON (sandbox mode): no arguments
  - Unix socket binary (Docker mode): pass socket path as argument
"""
from __future__ import annotations

import json
import socket
import struct
import sys
import time
import traceback
import types
from dataclasses import dataclass

# Protocol constants
CMD_MOVE = 0
CMD_QUIT = 1
MOVE_NONE = 255
STATUS_OK = 0
STATUS_ERROR = 1


@dataclass(frozen=True, slots=True)
class RoundState:
    """Game state passed to the bot."""
    round_index: int
    my_history: tuple[int, ...]
    opponent_history: tuple[int, ...]


# Fake nashium module setup
_m = types.ModuleType('nashium')
_mc = types.ModuleType('nashium.core')
_m.RoundState = _mc.RoundState = RoundState
_m.core = _mc
sys.modules['nashium'] = _m
sys.modules['nashium.core'] = _mc


class BotRunner:
    """Manages bot instance and game state."""
    __slots__ = ('bot', 'my_history', 'opponent_history', 'round_index')

    def __init__(self, bot):
        self.bot = bot
        self.my_history: list[int] = []
        self.opponent_history: list[int] = []
        self.round_index: int = 0

    def reset(self) -> None:
        self.my_history.clear()
        self.opponent_history.clear()
        self.round_index = 0

    def make_move(self, opponent_last_move: int | None) -> tuple[int, float]:
        if opponent_last_move is not None:
            self.opponent_history.append(opponent_last_move)

        state = RoundState(
            round_index=self.round_index,
            my_history=tuple(self.my_history),
            opponent_history=tuple(self.opponent_history),
        )

        start = time.perf_counter()
        move = int(self.bot.move(state))
        elapsed = time.perf_counter() - start

        self.my_history.append(move)
        self.round_index += 1

        return move, elapsed


def load_bot(code: str, seed: int | None = None):
    ns = {"__name__": "__bot__", "RoundState": RoundState}
    exec(compile(code, "<bot>", "exec"), ns)

    cls = ns.get("Bot")
    if cls is None:
        for name, obj in ns.items():
            if isinstance(obj, type) and callable(getattr(obj, "move", None)) and name != "RoundState":
                cls = obj
                break

    if cls is None:
        raise ValueError("No Bot class found. Define a class named 'Bot' with a 'move' method.")

    if seed is not None:
        try:
            return cls(seed=seed)
        except TypeError:
            try:
                return cls(seed)
            except TypeError:
                pass
    return cls()

# ============================================================
# SOCKET MODE (for Docker - Unix socket binary protocol)
# ============================================================

def socket_recvall(sock: socket.socket, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data.extend(chunk)
    return bytes(data)


def socket_send_response(sock: socket.socket, status: int, move: int, elapsed: float):
    sock.sendall(bytes([status, move]) + struct.pack(">d", elapsed))


def run_socket_mode(sock_path: str):
    """Unix socket binary protocol for Docker mode."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    for attempt in range(10):
        try:
            sock.connect(sock_path)
            break
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.1)
    else:
        print(f"Failed to connect to {sock_path}", file=sys.stderr)
        sys.exit(1)

    runner: BotRunner | None = None

    try:
        while True:
            if runner is None:
                # Load command: length-prefixed JSON
                raw_len = socket_recvall(sock, 4)
                length = struct.unpack(">I", raw_len)[0]
                data = socket_recvall(sock, length)
                cmd = json.loads(data)

                if cmd.get("cmd") == "load":
                    try:
                        bot = load_bot(cmd["code"], cmd.get("seed"))
                        runner = BotRunner(bot)
                        resp = json.dumps({"status": "ok"}).encode()
                    except Exception as e:
                        resp = json.dumps({
                            "status": "error",
                            "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                        }).encode()
                    sock.sendall(struct.pack(">I", len(resp)) + resp)
                continue

            # Move commands: binary protocol
            header = socket_recvall(sock, 2)
            cmd_byte, opp_byte = header[0], header[1]

            if cmd_byte == CMD_QUIT:
                break

            if cmd_byte == CMD_MOVE:
                opp_last = None if opp_byte == MOVE_NONE else opp_byte
                try:
                    mv, elapsed = runner.make_move(opp_last)
                    socket_send_response(sock, STATUS_OK, mv, elapsed)
                except Exception:
                    # Print the error so Docker captures it in the logs
                    traceback.print_exc(file=sys.stderr)
                    sys.stderr.flush()

                    # Continue sending the standard 10-byte error response
                    socket_send_response(sock, STATUS_ERROR, 0, 0.0)

    except ConnectionError:
        pass
    finally:
        sock.close()


# ============================================================
# MAIN
# ============================================================

def main():
    if len(sys.argv) < 2:
        print("Error: Socket path argument required", file=sys.stderr)
        sys.exit(1)

    # Docker mode: Unix socket
    run_socket_mode(sys.argv[1])


if __name__ == "__main__":
    main()