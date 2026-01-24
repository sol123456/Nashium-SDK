from __future__ import annotations

import time
from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import RoundState


@runtime_checkable
class BotExecutor(Protocol):
    """Protocol for bot execution strategies."""

    def get_move(self, state: "RoundState") -> int:
        """Get the bot's move for the given state. Returns 0 if timed out."""
        ...

    @property
    def elapsed_time(self) -> float:
        """Total time spent by the bot computing moves."""
        ...

    @property
    def timed_out(self) -> bool:
        """Whether the bot has exceeded its time limit."""
        ...

    def close(self) -> None:
        """Clean up any resources."""
        ...

    def __enter__(self) -> "BotExecutor": ...
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...


class LocalExecutor:
    """Executes a bot directly in the current process.

    Suitable for local testing with trusted code.
    """

    def __init__(self, bot, time_limit: float = float("inf")):
        self._bot = bot
        self._time_limit = time_limit
        self._elapsed_time = 0.0
        self._timed_out = False

    def get_move(self, state: "RoundState") -> int:
        from .errors import InvalidMoveError

        if self._timed_out:
            return 0

        start = time.perf_counter()
        try:
            move = int(self._bot.move(state))
        finally:
            self._elapsed_time += time.perf_counter() - start

        if self._elapsed_time > self._time_limit:
            self._timed_out = True

        if move not in (0, 1):
            raise InvalidMoveError(
                f"Bot returned invalid move {move!r} on round {state.round_index}. "
                "Expected 0 or 1."
            )

        return move

    @property
    def elapsed_time(self) -> float:
        return self._elapsed_time

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def close(self) -> None:
        pass

    def __enter__(self) -> "LocalExecutor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()