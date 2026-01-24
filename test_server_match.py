#!/usr/bin/env python3
"""Test script for server-side match execution."""

import time
from nashium.cli.sample_bots import (
    run_match_from_code,
    load_bot_from_code,
    run_match,
    sample_leaderboard_bot_sources,
    AlwaysTails,
    AlwaysHeads,
    RANDOM_BOT,
)
from nashium.core import MatchConfig


def test_basic():
    print("=" * 60)
    print("Test 1: Quick sanity check (100 rounds)")
    print("=" * 60)
    config = MatchConfig(rounds=100)
    start = time.perf_counter()
    result = run_match_from_code(ALWAYS_HEADS, ALWAYS_TAILS, config)
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
    result = run_match_from_code(ALWAYS_HEADS, ALWAYS_TAILS, config)
    elapsed = time.perf_counter() - start
    print(f"Result: {result.result}")
    print(f"Submitted wins: {result.submitted_wins}/{result.rounds}")
    print(f"Wall time: {elapsed:.3f}s")
    print(f"Rounds per second: {result.rounds / elapsed:.0f}")
    print()


def test_with_seed():
    print("=" * 60)
    print("Test 3: Random bot with seed (deterministic)")
    print("=" * 60)
    config = MatchConfig(rounds=100)

    # Run twice with same seed - should get same result
    result1 = run_match_from_code(RANDOM_BOT, ALWAYS_HEADS, config, seed=12345)
    result2 = run_match_from_code(RANDOM_BOT, ALWAYS_HEADS, config, seed=12345)

    print(f"Run 1: {result1.submitted_wins} wins")
    print(f"Run 2: {result2.submitted_wins} wins")
    print(f"Deterministic: {result1.submitted_wins == result2.submitted_wins}")
    print()


def test_sample_bots():
    print("=" * 60)
    print("Test 4: All sample bots")
    print("=" * 60)

    config = MatchConfig(rounds=100)
    submitted = ALWAYS_TAILS  # Simple test bot

    for name, code in sample_leaderboard_bot_sources():
        result = run_match_from_code(submitted, code, config)
        print(f"vs {name}: {result.submitted_wins}/100 wins ({result.result})")
    print()


def test_executor_api():
    print("=" * 60)
    print("Test 5: Direct executor API (like CLI)")
    print("=" * 60)

    config = MatchConfig(rounds=100)

    # This mirrors how CLI does it with load_bot_from_file
    with load_bot_from_code(ALWAYS_HEADS, seed=42) as submitted:
        with load_bot_from_code(RANDOM_BOT, seed=42) as opponent:
            result = run_match(submitted, opponent, config)
            print(f"Result: {result.result}")
            print(f"Submitted wins: {result.submitted_wins}/{result.rounds}")
    print()


def test_timeout():
    print("=" * 60)
    print("Test 6: Timeout handling - infinite loop")
    print("=" * 60)

    BOT_INFINITE = """
class Bot:
    def move(self, state):
        while True:
            pass
"""

    start = time.perf_counter()
    try:
        with load_bot_from_code(BOT_INFINITE, time_limit=100, move_timeout=2.0) as bad_bot:
            with load_bot_from_code(ALWAYS_HEADS, time_limit=100, move_timeout=2.0) as good_bot:
                # Try to get a move - should timeout
                move = bad_bot.get_move(None)
                print(f"Move: {move}, Timed out: {bad_bot.timed_out}")
    except Exception as e:
        print(f"Exception: {type(e).__name__}: {e}")

    elapsed = time.perf_counter() - start
    print(f"Wall time: {elapsed:.3f}s (expected ~2s)")
    print()


def main():
    test_basic()
    test_performance()
    test_with_seed()
    test_sample_bots()
    test_executor_api()
    test_timeout()


if __name__ == "__main__":
    main()