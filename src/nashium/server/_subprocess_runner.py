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
# STDIO MODE (for sandbox - stdin/stdout JSON)
# ============================================================

def stdio_send(data: dict) -> None:
    print(json.dumps(data), flush=True)


def stdio_recv() -> dict | None:
    try:
        line = sys.stdin.readline()
        if not line:
            return None
        return json.loads(line.strip())
    except Exception as e:
        stdio_send({"status": "error", "error": f"Failed to read input: {e}"})
        return None


def run_stdio_mode():
    """Original stdin/stdout JSON protocol for subprocess/sandbox mode."""
    runner: BotRunner | None = None

    while True:
        cmd = stdio_recv()
        if cmd is None:
            break

        command = cmd.get("cmd")

        if command == "quit":
            break

        elif command == "load":
            try:
                bot = load_bot(cmd["code"], seed=cmd.get("seed"))
                runner = BotRunner(bot)
                stdio_send({"status": "ok"})
            except Exception as e:
                stdio_send({
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                })

        elif command == "reset":
            if runner is None:
                stdio_send({"status": "error", "error": "Bot not loaded"})
            else:
                runner.reset()
                stdio_send({"status": "ok"})

        elif command == "move":
            if runner is None:
                stdio_send({"status": "error", "error": "Bot not loaded"})
                continue
            try:
                move, elapsed = runner.make_move(cmd.get("opponent_last"))
                stdio_send({"status": "ok", "move": move, "time": elapsed})
            except Exception as e:
                stdio_send({
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                })

        else:
            stdio_send({"status": "error", "error": f"Unknown command: {command}"})


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
                    socket_send_response(sock, STATUS_ERROR, 0, 0.0)

    except ConnectionError:
        pass
    finally:
        sock.close()


# ============================================================
# MAIN
# ============================================================

def main():
    if len(sys.argv) >= 2:
        # Docker mode: Unix socket
        run_socket_mode(sys.argv[1])
    else:
        # Sandbox mode: stdin/stdout
        run_stdio_mode()


if __name__ == "__main__":
    main()