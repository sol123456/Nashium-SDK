from __future__ import annotations

from .engine import RoundState

# PLEASE DON'T CHANGE THESE BOTS. OTHERWISE, YOU MAY PASS THE QUALIFICATION BUT STILL BE REJECTED BY THE SERVER
class AlwaysHeads:
    def move(self, state: RoundState) -> int:
        return 0


class AlwaysTails:
    def move(self, state: RoundState) -> int:
        return 1


class Alternator:
    def move(self, state: RoundState) -> int:
        return state.round_index % 2


class MirrorOpponent:
    def move(self, state: RoundState) -> int:
        if not state.opponent_history:
            return 0
        return state.opponent_history[-1]


class FrequencyCounter:
    def move(self, state: RoundState) -> int:
        if not state.opponent_history:
            return 0
        heads = sum(1 for x in state.opponent_history if x == 0)
        tails = len(state.opponent_history) - heads
        return 0 if heads >= tails else 1


def sample_leaderboard_bots(seed: int) -> list[tuple[str, object]]:
    return [
        ("always_heads", AlwaysHeads()),
        ("always_tails", AlwaysTails()),
        ("alternator", Alternator()),
        ("mirror", MirrorOpponent()),
        ("frequency_counter", FrequencyCounter()),
    ]
