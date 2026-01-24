#!/usr/bin/env python3
"""
Runner script for sandboxed bot execution.
Communicates via JSON over stdin/stdout.

This runs inside Docker containers or subprocesses.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from dataclasses import dataclass


@dataclass(frozen=True)
class RoundState:
    """Game state passed to the bot."""
    round_index: int
    my_history: tuple[int, ...]
    opponent_history: tuple[int, ...]


def send(data: dict) -> None:
    """Send JSON response to stdout."""
    print(json.dumps(data), flush=True)


def recv() -> dict | None:
    """Read JSON command from stdin."""
    try:
        line = sys.stdin.readline()
        if not line:
            return None
        return json.loads(line.strip())
    except:
        return None


def load_bot(code: str):
    """Load and instantiate a bot from source code."""
    namespace = {"__name__": "__bot__"}

    # Inject RoundState so bots can use it
    namespace["RoundState"] = RoundState

    exec(compile(code, "<bot>", "exec"), namespace)

    # Look for Bot class
    bot_class = namespace.get("Bot")

    if bot_class is None:
        # Find any class with a move method
        for name, obj in namespace.items():
            if (
                isinstance(obj, type)
                and callable(getattr(obj, "move", None))
                and name not in ("RoundState",)
            ):
                bot_class = obj
                break

    if bot_class is None:
        raise ValueError(
            "No Bot class found. Define a class named 'Bot' with a 'move' method."
        )

    return bot_class()


def run_move(bot, state: RoundState) -> tuple[int, float]:
    """Execute a single move and return (move, time_taken)."""
    start = time.perf_counter()
    move = int(bot.move(state))
    elapsed = time.perf_counter() - start
    return move, elapsed


def main():
    bot = None

    while True:
        cmd = recv()
        if cmd is None:
            break

        command = cmd.get("cmd")

        if command == "quit":
            break

        elif command == "load":
            try:
                bot = load_bot(cmd["code"])
                send({"status": "ok"})
            except Exception as e:
                send({
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                })

        elif command == "move":
            if bot is None:
                send({"status": "error", "error": "Bot not loaded"})
                continue

            try:
                state = RoundState(
                    round_index=cmd["round_index"],
                    my_history=tuple(cmd["my_history"]),
                    opponent_history=tuple(cmd["opponent_history"]),
                )
                move, elapsed = run_move(bot, state)
                send({"status": "ok", "move": move, "time": elapsed})
            except Exception as e:
                send({
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                })

        else:
            send({"status": "error", "error": f"Unknown command: {command}"})


if __name__ == "__main__":
    main()