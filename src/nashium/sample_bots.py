from __future__ import annotations

import random

from .engine import RoundState

# PLEASE NOTE: If you change these bots, the qualification code may not correctly test your bot.
class AlwaysHeads:
    """Always plays 0 (Heads). Trivial to beat."""

    def move(self, state: RoundState) -> int:
        return 0


class AlwaysTails:
    """Always plays 1 (Tails). Trivial to beat."""

    def move(self, state: RoundState) -> int:
        return 1


class Alternator:
    """Alternates between 0 and 1 each round. Predictable."""

    def move(self, state: RoundState) -> int:
        return state.round_index % 2


class MirrorOpponent:
    """Copies what you played last round."""

    def move(self, state: RoundState) -> int:
        if not state.opponent_history:
            return 0
        return state.opponent_history[-1]


class FrequencyCounter:
    """Plays whatever you've played most often."""

    def move(self, state: RoundState) -> int:
        if not state.opponent_history:
            return 0
        heads = sum(1 for x in state.opponent_history if x == 0)
        tails = len(state.opponent_history) - heads
        return 0 if heads >= tails else 1


class RandomBot:
    """Plays randomly. Hard to beat consistently."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    def move(self, state: RoundState) -> int:
        return self.rng.randint(0, 1)


def sample_leaderboard_bots(seed: int) -> list[tuple[str, object]]:
    """
    Returns the sample bots used for local qualification testing.
    These are intentionally simple - the real leaderboard bots will be harder!
    """
    return [
        ("always_heads", AlwaysHeads()),
        ("always_tails", AlwaysTails()),
        ("alternator", Alternator()),
        ("mirror", MirrorOpponent()),
        ("frequency_counter", FrequencyCounter()),
        # Note: RandomBot not included because you can't reliably beat true random
    ]