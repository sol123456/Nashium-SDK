from __future__ import annotations

import argparse
from pathlib import Path

from .client import NashiumClient, NashiumClientConfig
from .formatting import (
    Colors,
    format_result,
    format_time_warning,
    print_dim,
    print_failure,
    print_header,
    print_info,
    print_success,
)
from .loading import load_bot_from_file
from .sample_bots import determinism_test_bot_factories, sample_leaderboard_bots
from ..core import InteractionResult, MatchConfig, run_match, run_match_trace
from ..core.util import stable_seed


def _read_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


# ============================================================================
# SCAFFOLD COMMAND
# ============================================================================

SCAFFOLD_TEMPLATE = '''"""
Nashium Bot Template
====================

Welcome! This is your bot file. Your goal is to PREDICT what your opponent
will play (Heads=0 or Tails=1) in a matching pennies game.

HOW THE GAME WORKS:
-------------------
- Each round, both you and your opponent choose either 0 (Heads) or 1 (Tails)
- If you MATCH your opponent's choice, YOU WIN that round
- If you DON'T match, your opponent wins
- You play 10,000 rounds per match
- To qualify, you need to win >51.55% of rounds (statistically significant)

WHAT YOU HAVE ACCESS TO:
------------------------
In the `move()` method, you receive a `state` object with:

    state.round_index       - Current round number (0 to 9,999)
    state.my_history        - Tuple of YOUR past moves, e.g., (0, 1, 1, 0, ...)
    state.opponent_history  - Tuple of OPPONENT'S past moves

RULES:
------
1. Your bot MUST be deterministic given the same seed
2. You have 100 seconds TOTAL for all 10,000 moves (not per move!)
3. You must return either 0 or 1 from move()
4. Don't use external resources (network, files, etc.)

TIPS:
-----
- Look for patterns in state.opponent_history
- Simple strategies often beat complex ones
- Test locally with: nashium qualify my_bot.py
- Check determinism with: nashium check my_bot.py

EXAMPLE PATTERNS TO DETECT:
---------------------------
- Does opponent always play the same thing?
- Does opponent alternate?
- Does opponent copy your last move?
- Does opponent play the opposite of your last move?
"""

import random
from nashium import RoundState


class MyBot:
    def __init__(self, seed: int):
        """
        Called once when your bot is created.

        Args:
            seed: A random seed for reproducibility. Use this to initialize
                  any random number generators so your bot is deterministic.
        """
        # Always use the provided seed for randomness!
        self.rng = random.Random(seed)

        # You can store any state you need here
        self.opponent_patterns = {}

    def move(self, state: RoundState) -> int:
        """
        Called each round to get your move.

        Args:
            state: Contains round_index, my_history, and opponent_history

        Returns:
            0 for Heads, 1 for Tails (your PREDICTION of what opponent will play)
        """
        # Round 0: No history yet, just guess
        if state.round_index == 0:
            return self.rng.choice([0, 1])

        # Simple strategy: Predict opponent will repeat their last move
        # This beats "always same" and "repeat last" opponents
        last_opponent_move = state.opponent_history[-1]

        # TODO: Replace this with your own strategy!
        # Ideas:
        #   - Track opponent move frequencies
        #   - Look for alternating patterns
        #   - Detect if opponent is mirroring you
        #   - Use more sophisticated pattern matching

        return last_opponent_move


def create_bot(seed: int):
    """
    Factory function - must return an instance of your bot.
    Don't change the function signature!
    """
    return MyBot(seed)
'''


def cmd_scaffold(args: argparse.Namespace) -> int:
    target = Path(args.path)

    if target.exists():
        print_failure(f"File already exists: {target}")
        print_info("Choose a different filename or delete the existing file.")
        return 1

    target.write_text(SCAFFOLD_TEMPLATE, encoding="utf-8")

    print_header("Bot Template Created!")
    print_success(f"Created: {target}")
    print()
    print("  Next steps:")
    print(f"    1. Edit {Colors.CYAN}{target}{Colors.RESET} with your strategy")
    print(f"    2. Test it: {Colors.CYAN}nashium qualify {target}{Colors.RESET}")
    print(f"    3. Check determinism: {Colors.CYAN}nashium check {target}{Colors.RESET}")
    print()
    return 0


# ============================================================================
# DETERMINISM CHECK (shared by 'check' and 'qualify')
# ============================================================================

def run_determinism_check(bot_path: Path, seed: int, config: MatchConfig, verbose: bool = True) -> tuple[
    bool, list[str]]:
    """
    Run determinism check against test bots including RandomBot.

    Returns:
        (is_deterministic, list of failed opponent names)
    """
    # Get bot FACTORIES, not instances!
    # This is crucial: we need fresh opponent instances for each run
    opponent_factories = determinism_test_bot_factories()
    failed_opponents = []

    if verbose:
        print_info("Checking determinism (running your bot twice with same seed)...")
        print()

    for name, create_opponent in opponent_factories:
        # Run 1: Create fresh instances of BOTH user bot and opponent
        bot_1 = load_bot_from_file(bot_path, seed)
        opponent_1 = create_opponent(seed)
        trace_1 = run_match_trace(bot_1, opponent_1, config)

        # Run 2: Create fresh instances again - this is the key fix!
        bot_2 = load_bot_from_file(bot_path, seed)
        opponent_2 = create_opponent(seed)  # Fresh opponent with reset RNG
        trace_2 = run_match_trace(bot_2, opponent_2, config)

        same = trace_1.submitted_moves == trace_2.submitted_moves

        if verbose:
            if same:
                print_success(f"vs {name}: Deterministic ✓")
            else:
                print_failure(f"vs {name}: NOT deterministic!")
                # Find first difference
                for i, (m1, m2) in enumerate(zip(trace_1.submitted_moves, trace_2.submitted_moves)):
                    if m1 != m2:
                        print_dim(f"First difference at round {i}: got {m1} then {m2}")
                        break

        if not same:
            failed_opponents.append(name)

    return (len(failed_opponents) == 0, failed_opponents)


# ============================================================================
# QUALIFY COMMAND
# ============================================================================

def cmd_qualify(args: argparse.Namespace) -> int:
    submitted_path = Path(args.bot)

    if not submitted_path.exists():
        print_failure(f"Bot file not found: {submitted_path}")
        return 1

    print_header(f"Qualifying: {submitted_path.name}")

    seed = args.seed
    if seed is None:
        seed = stable_seed(_read_bytes(submitted_path), b"sample_leaderboard")

    config = MatchConfig(
        rounds=args.rounds,
        invert_opponent=args.invert_opponent,
        max_total_time_seconds_per_bot=args.time_budget,
    )

    # =========== STEP 1: Determinism Check ===========
    determinism_config = MatchConfig(
        rounds=2000,  # Shorter for determinism check
        invert_opponent=args.invert_opponent,
        max_total_time_seconds_per_bot=args.time_budget,
    )

    is_deterministic, failed_det_opponents = run_determinism_check(
        submitted_path, seed, determinism_config, verbose=False
    )

    if is_deterministic:
        print_success("Determinism check passed")
    else:
        print_failure(f"Determinism check FAILED (vs: {', '.join(failed_det_opponents)})")
    print()

    # =========== STEP 2: Performance Tests ===========
    opponents = sample_leaderboard_bots(seed)

    results = []
    max_time_used = 0.0
    all_pass = True

    for name, opp in opponents:
        submitted = load_bot_from_file(submitted_path, seed)
        summary = run_match(submitted, opp, config)

        max_time_used = max(max_time_used, summary.submitted_time_seconds)
        passed = summary.result == InteractionResult.S_WIN and summary.stat_sig

        status, explanation = format_result(
            summary.result, summary.stat_sig, summary.submitted_wins, summary.rounds
        )

        results.append({
            'name': name,
            'passed': passed,
            'status': status,
            'explanation': explanation,
            'wins': summary.submitted_wins,
            'rounds': summary.rounds,
            'win_rate': summary.submitted_win_rate,
            'time': summary.submitted_time_seconds,
        })

        if not passed:
            all_pass = False

    # Print results table
    print(f"  {'Opponent':<20} {'Result':<25} {'Win Rate':<12} {'Time':<10}")
    print(f"  {'-' * 20} {'-' * 25} {'-' * 12} {'-' * 10}")

    for r in results:
        win_rate_str = f"{r['win_rate'] * 100:.1f}%"
        time_str = f"{r['time']:.2f}s"

        if r['passed']:
            icon = f"{Colors.GREEN}✓{Colors.RESET}"
        else:
            icon = f"{Colors.RED}✗{Colors.RESET}"

        print(f"  {icon} {r['name']:<18} {r['status']:<35} {win_rate_str:<12} {time_str:<10}")

    print()

    # Time analysis
    print(f"  {Colors.BOLD}Time Analysis:{Colors.RESET}")
    print(f"    {format_time_warning(max_time_used, args.time_budget)}")
    print()

    # =========== FINAL VERDICT ===========
    qualified = all_pass and is_deterministic

    if qualified:
        print_header("🎉 QUALIFIED!")
        print_success("Your bot beat all test opponents with statistical significance!")
        print_success("Your bot is deterministic!")
        print()
        print("  Your bot is ready to submit:")
        print(
            f"    {Colors.CYAN}nashium upload {submitted_path} --name \"My Bot\" --base-url https://nashium.com{Colors.RESET}")
        print()
        return 0
    else:
        print_header("❌ DID NOT QUALIFY")

        # List all failure reasons
        failure_reasons = []

        if not is_deterministic:
            failure_reasons.append(f"  {Colors.RED}•{Colors.RESET} Your bot is NOT deterministic")

        failed_opponents = [r['name'] for r in results if not r['passed']]
        if failed_opponents:
            failure_reasons.append(f"  {Colors.RED}•{Colors.RESET} Failed to beat: {', '.join(failed_opponents)}")

        print()
        print("  Reasons for failure:")
        for reason in failure_reasons:
            print(reason)
        print()

        if not is_deterministic:
            print(f"  {Colors.BOLD}Determinism:{Colors.RESET}")
            print("    Your bot must produce identical moves when given the same seed.")
            print(f"    Run {Colors.CYAN}nashium check {submitted_path.name}{Colors.RESET} for detailed diagnostics.")
            print()

        if failed_opponents:
            print(f"  {Colors.BOLD}Winning requirement:{Colors.RESET}")
            print(
                f"    Win more than {Colors.BOLD}51.55%{Colors.RESET} of rounds against {Colors.BOLD}each{Colors.RESET} opponent")
            print("    (at least 5,155 out of 10,000 rounds)")
            print()
            print("  Tips:")
            print("    • 'always_heads' always plays 0, 'always_tails' always plays 1")
            print("    • 'mirror' copies your last move")
            print("    • 'alternator' switches between 0 and 1 each round")
            print()

        return 2


# ============================================================================
# CHECK COMMAND (detailed determinism)
# ============================================================================

def cmd_check(args: argparse.Namespace) -> int:
    submitted_path = Path(args.bot)

    if not submitted_path.exists():
        print_failure(f"Bot file not found: {submitted_path}")
        return 1

    print_header(f"Checking Determinism: {submitted_path.name}")

    seed = args.seed
    if seed is None:
        seed = stable_seed(_read_bytes(submitted_path), b"determinism_check")

    config = MatchConfig(
        rounds=args.rounds,
        invert_opponent=args.invert_opponent,
        max_total_time_seconds_per_bot=args.time_budget,
    )

    is_deterministic, failed_opponents = run_determinism_check(submitted_path, seed, config, verbose=True)

    print()

    if is_deterministic:
        print_header("✓ DETERMINISTIC")
        print_success("Your bot produces identical moves when given the same seed.")
        print()
        print("  This is required for fair competition. Your bot is ready!")
        print()
        return 0
    else:
        print_header("✗ NOT DETERMINISTIC")
        print_failure("Your bot produces DIFFERENT moves when run twice with the same seed!")
        print()
        print("  This is not allowed. Common causes:")
        print(
            f"    • Using {Colors.YELLOW}random.random(){Colors.RESET} instead of {Colors.GREEN}self.rng.random(){Colors.RESET}")
        print(f"    • Using {Colors.YELLOW}time.time(){Colors.RESET} or other external state")
        print(f"    • Using {Colors.YELLOW}dict{Colors.RESET} iteration (order can vary in older Python)")
        print()
        print("  Fix: Use the seed provided in __init__ for ALL randomness:")
        print(f"    {Colors.CYAN}self.rng = random.Random(seed){Colors.RESET}")
        print(f"    {Colors.CYAN}choice = self.rng.choice([0, 1]){Colors.RESET}")
        print()
        return 3


# ============================================================================
# RUN COMMAND
# ============================================================================

def cmd_run(args: argparse.Namespace) -> int:
    a_path = Path(args.bot_a)
    b_path = Path(args.bot_b)

    if not a_path.exists():
        print_failure(f"Bot file not found: {a_path}")
        return 1
    if not b_path.exists():
        print_failure(f"Bot file not found: {b_path}")
        return 1

    print_header(f"Match: {a_path.name} vs {b_path.name}")

    seed = args.seed
    if seed is None:
        seed = stable_seed(_read_bytes(a_path), _read_bytes(b_path))

    bot_a = load_bot_from_file(a_path, seed)
    bot_b = load_bot_from_file(b_path, seed)

    config = MatchConfig(
        rounds=args.rounds,
        invert_opponent=args.invert_opponent,
        max_total_time_seconds_per_bot=args.time_budget,
    )

    summary = run_match(bot_a, bot_b, config)

    status, explanation = format_result(
        summary.result, summary.stat_sig, summary.submitted_wins, summary.rounds
    )

    print(f"  {Colors.BOLD}Results:{Colors.RESET}")
    print(f"    Rounds played:     {summary.rounds:,}")
    print(f"    Bot A wins:        {summary.submitted_wins:,} ({summary.submitted_win_rate * 100:.2f}%)")
    print(
        f"    Bot B wins:        {summary.rounds - summary.submitted_wins:,} ({(1 - summary.submitted_win_rate) * 100:.2f}%)")
    print()
    print(f"    Result:            {status}")
    print(f"    {explanation}")
    print()
    print(f"  {Colors.BOLD}Performance:{Colors.RESET}")
    print(f"    Bot A time:        {summary.submitted_time_seconds:.3f}s")
    print(f"    Bot B time:        {summary.leaderboard_time_seconds:.3f}s")
    print(f"    Total wall time:   {summary.wall_time_seconds:.3f}s")
    print()

    return 0


# ============================================================================
# SEED COMMAND
# ============================================================================

def cmd_seed(args: argparse.Namespace) -> int:
    a_path = Path(args.bot_a)
    b_path = Path(args.bot_b)

    if not a_path.exists():
        print_failure(f"File not found: {a_path}")
        return 1
    if not b_path.exists():
        print_failure(f"File not found: {b_path}")
        return 1

    seed = stable_seed(_read_bytes(a_path), _read_bytes(b_path))
    print(f"Seed for {a_path.name} vs {b_path.name}: {seed}")
    return 0


# ============================================================================
# UPLOAD COMMAND
# ============================================================================

def cmd_upload(args: argparse.Namespace) -> int:
    bot_path = Path(args.bot)

    if not bot_path.exists():
        print_failure(f"Bot file not found: {bot_path}")
        return 1

    print_header(f"Uploading: {args.name}")

    code = bot_path.read_text(encoding="utf-8")

    client = NashiumClient(
        NashiumClientConfig(
            base_url=args.base_url,
            bearer_token=args.token,
            timeout_seconds=args.timeout_seconds,
        )
    )

    payload = {
        "name": args.name,
        "code": code,
    }

    try:
        resp = client.request_json("POST", args.endpoint, payload=payload)
        print_success("Bot uploaded successfully!")
        print()
        if resp:
            if 'botId' in resp:
                print(f"  Bot ID: {resp['botId']}")
            if 'queuePosition' in resp:
                print(f"  Queue position: {resp['queuePosition']}")
            if 'message' in resp:
                print(f"  {resp['message']}")
        print()
        return 0
    except Exception as e:
        print_failure(f"Upload failed: {e}")
        return 1