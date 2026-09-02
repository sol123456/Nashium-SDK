from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoundState:
    """State handed to a bot's move(). Mirrored inside the container.

    Kept here purely so bot authors can import it for type hints; the host
    never constructs one. No slots=True, so the host stays 3.9-compatible (R11).
    """
    round_index: int
    my_history: tuple[int, ...]
    opponent_history: tuple[int, ...]


@dataclass(frozen=True)
class MatchConfig:
    rounds: int = 10_000
    stat_sig_win_threshold: int = 5155

    max_total_time_seconds_per_bot: float = 100.0
    max_total_memory_bytes_per_bot: int = 200 * 1024 * 1024

    # Hang guard: a sleeping bot burns no CPU, so wall clock is capped too.
    max_total_wall_seconds_per_bot: float = 300.0