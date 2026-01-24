#!/usr/bin/env python3
"""
Runner script for sandboxed bot execution.
Communicates via JSON over stdin/stdout.
Maintains game state internally to minimize IPC overhead.
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


class BotRunner:
    """Manages bot instance and game state."""

    def __init__(self, bot):
        self.bot = bot
        self.my_history: list[int] = []
        self.opponent_history: list[int] = []
        self.round_index: int = 0

    def reset(self) -> None:
        """Reset state for a new match."""
        self.my_history.clear()
        self.opponent_history.clear()
        self.round_index = 0

    def make_move(self, opponent_last_move: int | None) -> tuple[int, float]:
        """
        Process opponent's last move and generate our move.
        Returns (move, time_taken).
        """
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
    except Exception as e:
        send({"status": "error", "error": f"Failed to read input: {e}"})
        return None


def load_bot(code: str, seed: int | None = None):
    """
    Load and instantiate a bot from source code.

    If seed is provided and the Bot class accepts a seed parameter,
    it will be passed to the constructor.
    """
    namespace = {"__name__": "__bot__"}
    namespace["RoundState"] = RoundState

    exec(compile(code, "<bot>", "exec"), namespace)

    bot_class = namespace.get("Bot")

    if bot_class is None:
        for name, obj in namespace.items():
            if (
                    isinstance(obj, type)
                    and callable(getattr(obj, "move", None))
                    and name != "RoundState"
            ):
                bot_class = obj
                break

    if bot_class is None:
        raise ValueError(
            "No Bot class found. Define a class named 'Bot' with a 'move' method."
        )

    # Try to instantiate with seed, fall back to no args
    if seed is not None:
        try:
            return bot_class(seed=seed)
        except TypeError:
            try:
                return bot_class(seed)
            except TypeError:
                pass

    return bot_class()


def main():
    runner: BotRunner | None = None

    while True:
        cmd = recv()
        if cmd is None:
            break

        command = cmd.get("cmd")

        if command == "quit":
            break

        elif command == "load":
            try:
                seed = cmd.get("seed")  # Optional seed
                bot = load_bot(cmd["code"], seed=seed)
                runner = BotRunner(bot)
                send({"status": "ok"})
            except Exception as e:
                send({
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                })

        elif command == "reset":
            if runner is None:
                send({"status": "error", "error": "Bot not loaded"})
            else:
                runner.reset()
                send({"status": "ok"})

        elif command == "move":
            if runner is None:
                send({"status": "error", "error": "Bot not loaded"})
                continue

            try:
                opponent_last = cmd.get("opponent_last")
                move, elapsed = runner.make_move(opponent_last)
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