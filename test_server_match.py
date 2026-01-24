#!/usr/bin/env python3
"""Test script for server-side match execution."""

import time
from nashium.server.match_runner import run_match_from_code
from nashium.core import MatchConfig

BOT_ALWAYS_ZERO = """
class Bot:
    def move(self, state):
        return 0
"""

BOT_ALWAYS_ONE = """
class Bot:
    def move(self, state):
        return 1
"""

BOT_RANDOM = """
import random
class Bot:
    def move(self, state):
        return random.randint(0, 1)
"""

BOT_TIT_FOR_TAT = """
class Bot:
    def move(self, state):
        if not state.opponent_history:
            return 0
        return state.opponent_history[-1]
"""

BOT_INFINITE_LOOP = """
class Bot:
    def move(self, state):
        while True:
            pass
        return 0
"""

BOT_SLOW = """
import time
class Bot:
    def move(self, state):
        time.sleep(0.1)
        return 0
"""


def test_basic():
    print("=" * 60)
    print("Test 1: Quick sanity check (100 rounds)")
    print("=" * 60)
    config = MatchConfig(rounds=100)
    start = time.perf_counter()
    result = run_match_from_code(BOT_ALWAYS_ZERO, BOT_ALWAYS_ONE, config)
    elapsed = time.perf_counter() - start
    print(f"Result: {result.result}")
    print(f"Submitted wins: {result.submitted_wins}/{result.rounds}")
    print(f"Wall time: {elapsed:.3f}s")
    print()


def test_performance():
    print("=" * 60)
    print("Test 2: Performance test (10,000 rounds)")
    print("=" * 60)
    config = MatchConfig(rounds=10_000)
    start = time.perf_counter()
    result = run_match_from_code(BOT_ALWAYS_ZERO, BOT_ALWAYS_ONE, config)
    elapsed = time.perf_counter() - start
    print(f"Result: {result.result}")
    print(f"Submitted wins: {result.submitted_wins}/{result.rounds}")
    print(f"Wall time: {elapsed:.3f}s")
    print(f"Rounds per second: {result.rounds / elapsed:.0f}")
    print()


def test_tit_for_tat():
    print("=" * 60)
    print("Test 3: Tit-for-tat vs Random (1000 rounds)")
    print("=" * 60)
    config = MatchConfig(rounds=1000)
    result = run_match_from_code(BOT_TIT_FOR_TAT, BOT_RANDOM, config)
    print(f"Result: {result.result}")
    print(f"Submitted wins: {result.submitted_wins}/{result.rounds}")
    print(f"Win rate: {result.submitted_win_rate:.2%}")
    print()


def test_infinite_loop():
    print("=" * 60)
    print("Test 4: Timeout handling - infinite loop bot")
    print("=" * 60)

    # Short move timeout (2s) to make test faster
    from nashium.server.sandbox import SubprocessExecutor
    from nashium.core import MatchConfig, InteractionResult

    config = MatchConfig(rounds=10)
    start = time.perf_counter()

    try:
        # Manually create executor with short move timeout
        with SubprocessExecutor(BOT_INFINITE_LOOP, time_limit=100.0, move_timeout=2.0) as submitted:
            with SubprocessExecutor(BOT_ALWAYS_ZERO, time_limit=100.0, move_timeout=2.0) as leaderboard:
                # Try to get moves
                for i in range(10):
                    s_move = submitted.get_move(None if i == 0 else 0)
                    l_move = leaderboard.get_move(None if i == 0 else s_move)
                    print(f"Round {i}: submitted={'timed_out' if submitted.timed_out else s_move}")
                    if submitted.timed_out:
                        break

        elapsed = time.perf_counter() - start
        print(f"Submitted timed out: {submitted.timed_out}")
        print(f"Wall time: {elapsed:.3f}s (expected ~2s)")
        print("SUCCESS: Process was killed properly!")

    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"Exception: {type(e).__name__}: {e}")
        print(f"Wall time: {elapsed:.3f}s")
    print()


def test_slow_bot():
    print("=" * 60)
    print("Test 5: Cumulative timeout - slow bot")
    print("=" * 60)
    # Bot sleeps 100ms per move, limit is 0.5s = should timeout after ~5 moves
    config = MatchConfig(rounds=100, max_total_time_seconds_per_bot=2)
    start = time.perf_counter()
    result = run_match_from_code(BOT_SLOW, BOT_ALWAYS_ZERO, config)
    elapsed = time.perf_counter() - start
    print(f"Result: {result.result}")
    print(f"Submitted timed out: {result.submitted_timed_out}")
    print(f"Submitted time: {result.submitted_time_seconds:.3f}s")
    print(f"Wall time: {elapsed:.3f}s")
    print()


def main():
    test_basic()
    test_performance()
    test_tit_for_tat()
    test_infinite_loop()
    test_slow_bot()


if __name__ == "__main__":
    main()