from __future__ import annotations

import inspect
import random
from typing import Callable

from ..core import RoundState

###################################################################################################################
# PLEASE NOTE: If you change these bots, the qualification code may not correctly test your bot.
###################################################################################################################

class AlwaysHeads:
    """Always plays 0 (Heads). Trivial to beat."""

    def __init__(self, seed: int = None):
        pass  # No randomness needed

    def move(self, state: RoundState) -> int:
        return 0


class AlwaysTails:
    """Always plays 1 (Tails). Trivial to beat."""

    def __init__(self, seed: int = None):
        pass

    def move(self, state: RoundState) -> int:
        return 1


class Alternator:
    """Alternates between 0 and 1 each round. Predictable."""

    def __init__(self, seed: int = None):
        pass

    def move(self, state: RoundState) -> int:
        return state.round_index % 2


class MirrorOpponent:
    """Copies what you played last round."""

    def __init__(self, seed: int = None):
        pass

    def move(self, state: RoundState) -> int:
        if not state.opponent_history:
            return 0
        return state.opponent_history[-1]


class FrequencyCounter:
    """Plays whatever you've played most often."""

    def __init__(self, seed: int = None):
        pass

    def move(self, state: RoundState) -> int:
        if not state.opponent_history:
            return 0
        heads = sum(1 for x in state.opponent_history if x == 0)
        tails = len(state.opponent_history) - heads
        return 0 if heads >= tails else 1


class RandomBot:
    """Plays randomly. Used for determinism testing."""

    def __init__(self, seed: int = None):  # ← Make optional with default
        self.rng = random.Random(seed)

    def move(self, state: RoundState) -> int:
        return self.rng.randint(0, 1)


###################################################################################################################
# SOURCE CODE EXTRACTION - For sandboxed execution
###################################################################################################################

def _get_bot_source(bot_class: type) -> str:
    """
    Extract source code for a bot class.

    Includes necessary imports so the code can run standalone.
    """
    source = inspect.getsource(bot_class)
    return f"import random\n\n{source}\n"


def sample_leaderboard_bot_sources() -> list[tuple[str, str]]:
    """
    Returns (name, source_code) for sample leaderboard bots.
    """
    return [
        ("always_heads", _get_bot_source(AlwaysHeads)),
        ("always_tails", _get_bot_source(AlwaysTails)),
        ("alternator", _get_bot_source(Alternator)),
        ("mirror", _get_bot_source(MirrorOpponent)),
        ("frequency_counter", _get_bot_source(FrequencyCounter)),
    ]


def determinism_test_bot_sources() -> list[tuple[str, str]]:
    """
    Returns (name, source_code) for determinism testing bots.
    """
    return [
        ("always_heads", _get_bot_source(AlwaysHeads)),
        ("always_tails", _get_bot_source(AlwaysTails)),
        ("mirror_opponent", _get_bot_source(MirrorOpponent)),
        ("alternator", _get_bot_source(Alternator)),
        ("random", _get_bot_source(RandomBot)),
    ]