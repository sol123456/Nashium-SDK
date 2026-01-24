#!/usr/bin/env python3
"""Test script for server-side match execution."""

from nashium.server.match_runner import run_match_from_code
from nashium.core import MatchConfig

# Sample bot 1: Always plays 0
BOT_ALWAYS_ZERO = """
class Bot:
    def move(self, state):
        return 0
"""

# Sample bot 2: Always plays 1
BOT_ALWAYS_ONE = """
class Bot:
    def move(self, state):
        return 1
"""

# Sample bot 3: Random
BOT_RANDOM = """
import random

class Bot:
    def move(self, state):
        return random.randint(0, 1)
"""

# Sample bot 4: Tit-for-tat (copy opponent's last move)
BOT_TIT_FOR_TAT = """
class Bot:
    def move(self, state):
        if not state.opponent_history:
            return 0
        return state.opponent_history[-1]
"""

# Sample bot 5: Anti-tit-for-tat (opposite of opponent's last move)
BOT_ANTI_TIT_FOR_TAT = """
class Bot:
    def move(self, state):
        if not state.opponent_history:
            return 0
        return 1 - state.opponent_history[-1]
"""


def main():
    # Use fewer rounds for quick testing
    config = MatchConfig(rounds=10000)

    print("=" * 60)
    print("Test 1: Always-Zero vs Always-One")
    print("=" * 60)
    result = run_match_from_code(BOT_ALWAYS_ZERO, BOT_ALWAYS_ONE, config)
    print(f"Result: {result.result}")
    print(f"Submitted wins: {result.submitted_wins}/{result.rounds}")
    print(f"Win rate: {result.submitted_win_rate:.2%}")
    print(f"Submitted time: {result.submitted_time_seconds:.4f}s")
    print(f"Leaderboard time: {result.leaderboard_time_seconds:.4f}s")
    print()

    print("=" * 60)
    print("Test 2: Tit-for-Tat vs Random")
    print("=" * 60)
    result = run_match_from_code(BOT_TIT_FOR_TAT, BOT_RANDOM, config)
    print(f"Result: {result.result}")
    print(f"Submitted wins: {result.submitted_wins}/{result.rounds}")
    print(f"Win rate: {result.submitted_win_rate:.2%}")
    print()

    print("=" * 60)
    print("Test 3: Random vs Random")
    print("=" * 60)
    result = run_match_from_code(BOT_RANDOM, BOT_RANDOM, config)
    print(f"Result: {result.result}")
    print(f"Submitted wins: {result.submitted_wins}/{result.rounds}")
    print(f"Win rate: {result.submitted_win_rate:.2%}")
    print()

    print("=" * 60)
    print("Test 4: Longer match (1000 rounds)")
    print("=" * 60)
    config_long = MatchConfig(rounds=1000)
    result = run_match_from_code(BOT_ANTI_TIT_FOR_TAT, BOT_TIT_FOR_TAT, config_long)
    print(f"Result: {result.result}")
    print(f"Submitted wins: {result.submitted_wins}/{result.rounds}")
    print(f"Win rate: {result.submitted_win_rate:.2%}")
    print(f"Wall time: {result.wall_time_seconds:.4f}s")


if __name__ == "__main__":
    main()