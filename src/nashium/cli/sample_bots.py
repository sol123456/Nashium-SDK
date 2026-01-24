from __future__ import annotations

import random
from typing import Callable

from ..core import RoundState

###################################################################################################################
# PLEASE NOTE: If you change these bots, the qualification code may not correctly test your bot.
###################################################################################################################

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
    """Plays randomly. Used for determinism testing."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    def move(self, state: RoundState) -> int:
        return self.rng.randint(0, 1)


def sample_leaderboard_bots(seed: int) -> list[tuple[str, object]]:
    """
    Returns the sample bots used for local qualification testing.
    These are intentionally simple - the real leaderboard bots will be harder!

    Note: RandomBot is NOT included here because you can't reliably beat true random.
    """
    return [
        ("always_heads", AlwaysHeads()),
        ("always_tails", AlwaysTails()),
        ("alternator", Alternator()),
        ("mirror", MirrorOpponent()),
        ("frequency_counter", FrequencyCounter()),
    ]


# Type alias for bot factory functions
BotFactory = Callable[[int], object]


def determinism_test_bot_factories() -> list[tuple[str, BotFactory]]:
    """
    Returns FACTORIES for bots used in determinism testing.

    We return factories (functions that create bots) instead of instances
    because each test run needs a FRESH bot instance. If we reused the same
    RandomBot instance, its internal RNG state would be different on the
    second run, causing false "not deterministic" results.

    Includes RandomBot because it's the most important for catching non-determinism
    in the user's bot - if their bot uses unseeded randomness, it will produce
    different moves against the same random opponent sequence.
    """
    return [
        ("always_heads", lambda seed: AlwaysHeads()),
        ("alternator", lambda seed: Alternator()),
        ("random", lambda seed: RandomBot(seed)),  # Most important for determinism!
    ]