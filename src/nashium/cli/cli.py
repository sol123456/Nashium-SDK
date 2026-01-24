from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from .client import NashiumClient, NashiumClientConfig
from nashium.core.engine import InteractionResult, MatchConfig, run_match, run_match_trace
from nashium.core.errors import BotLoadError, BotTimeoutError, InvalidMoveError
from .loading import load_bot_from_file
from .sample_bots import sample_leaderboard_bots
from nashium.core.util import stable_seed


# ============================================================================
# ANSI Colors (works on most terminals, including Windows 10+)
# ============================================================================

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'

    @classmethod
    def disable(cls):
        cls.HEADER = cls.BLUE = cls.CYAN = cls.GREEN = ''
        cls.YELLOW = cls.RED = cls.BOLD = cls.DIM = cls.RESET = ''


# Disable colors if not a TTY (e.g., piping to file)
if not sys.stdout.isatty():
    Colors.disable()


def _print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'═' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}  {text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'═' * 60}{Colors.RESET}\n")


def _print_success(text: str):
    print(f"{Colors.GREEN}✓ {text}{Colors.RESET}")


def _print_failure(text: str):
    print(f"{Colors.RED}✗ {text}{Colors.RESET}")


def _print_warning(text: str):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.RESET}")


def _print_info(text: str):
    print(f"{Colors.BLUE}ℹ {text}{Colors.RESET}")


def _print_dim(text: str):
    print(f"{Colors.DIM}  {text}{Colors.RESET}")


def _read_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def _format_time_warning(max_time: float, budget: float = 100.0) -> str:
    """Generate a human-friendly time warning."""
    ratio = max_time / budget
    if ratio < 0.25:
        return f"{Colors.GREEN}Excellent!{Colors.RESET} Your bot is very fast ({max_time:.2f}s used of {budget:.0f}s budget)."
    elif ratio < 0.50:
        return f"{Colors.GREEN}Good.{Colors.RESET} Your bot has comfortable time margin ({max_time:.2f}s used of {budget:.0f}s budget)."
    elif ratio < 0.75:
        return f"{Colors.YELLOW}Caution.{Colors.RESET} Your bot is using significant time ({max_time:.2f}s of {budget:.0f}s). Consider optimizing."
    elif ratio < 0.90:
        return f"{Colors.YELLOW}Warning!{Colors.RESET} Your bot is close to the time limit ({max_time:.2f}s of {budget:.0f}s). Optimization recommended."
    else:
        return f"{Colors.RED}DANGER!{Colors.RESET} Your bot is very likely to timeout on the server ({max_time:.2f}s of {budget:.0f}s). Optimize before submitting!"


def _format_result(result: InteractionResult, stat_sig: bool, wins: int, rounds: int) -> tuple[str, str]:
    """Return (short_status, explanation)."""
    win_rate = wins / rounds * 100

    if result == InteractionResult.S_WIN and stat_sig:
        return (
            f"{Colors.GREEN}WIN{Colors.RESET}",
            f"You won {win_rate:.1f}% of rounds. This result is statistically significant."
        )
    elif result == InteractionResult.S_LOSS and stat_sig:
        return (
            f"{Colors.RED}LOSS{Colors.RESET}",
            f"You won only {win_rate:.1f}% of rounds. This result is statistically significant."
        )
    elif result == InteractionResult.DRAW:
        return (
            f"{Colors.YELLOW}DRAW{Colors.RESET}",
            f"You won exactly {win_rate:.1f}% of rounds. Perfect tie."
        )
    elif result == InteractionResult.STAT_DRAW_S_WIN:
        return (
            f"{Colors.YELLOW}DRAW{Colors.RESET} (leaning win)",
            f"You won {win_rate:.1f}% of rounds, but this is NOT statistically significant (need >51.55%)."
        )
    elif result == InteractionResult.STAT_DRAW_S_LOSS:
        return (
            f"{Colors.YELLOW}DRAW{Colors.RESET} (leaning loss)",
            f"You won {win_rate:.1f}% of rounds, but this is NOT statistically significant."
        )
    else:
        return (f"{Colors.DIM}UNKNOWN{Colors.RESET}", "")


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
        _print_failure(f"File already exists: {target}")
        _print_info("Choose a different filename or delete the existing file.")
        return 1

    target.write_text(SCAFFOLD_TEMPLATE, encoding="utf-8")

    _print_header("Bot Template Created!")
    _print_success(f"Created: {target}")
    print()
    print("  Next steps:")
    print(f"    1. Edit {Colors.CYAN}{target}{Colors.RESET} with your strategy")
    print(f"    2. Test it: {Colors.CYAN}nashium qualify {target}{Colors.RESET}")
    print(f"    3. Check determinism: {Colors.CYAN}nashium check {target}{Colors.RESET}")
    print()
    return 0


# ============================================================================
# QUALIFY COMMAND
# ============================================================================

def cmd_qualify(args: argparse.Namespace) -> int:
    submitted_path = Path(args.bot)

    if not submitted_path.exists():
        _print_failure(f"Bot file not found: {submitted_path}")
        return 1

    _print_header(f"Qualifying: {submitted_path.name}")

    seed = args.seed
    if seed is None:
        seed = stable_seed(_read_bytes(submitted_path), b"sample_leaderboard")

    opponents = sample_leaderboard_bots(seed)

    config = MatchConfig(
        rounds=args.rounds,
        invert_opponent=args.invert_opponent,
        max_total_time_seconds_per_bot=args.time_budget,
    )

    results = []
    max_time_used = 0.0
    all_pass = True

    for name, opp in opponents:
        try:
            submitted = load_bot_from_file(submitted_path, seed)
            summary = run_match(submitted, opp, config)
        except Exception as e:
            _print_failure(f"Error running against {name}: {e}")
            return 1

        max_time_used = max(max_time_used, summary.submitted_time_seconds)
        passed = summary.result == InteractionResult.S_WIN and summary.stat_sig

        status, explanation = _format_result(
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
    print(f"    {_format_time_warning(max_time_used, args.time_budget)}")
    print()

    # Final verdict
    if all_pass:
        _print_header("🎉 QUALIFIED!")
        _print_success("Your bot beat all test opponents with statistical significance!")
        print()
        print("  Your bot is ready to submit. Next steps:")
        print(f"    1. Verify determinism: {Colors.CYAN}nashium check {submitted_path}{Colors.RESET}")
        print(
            f"    2. Upload: {Colors.CYAN}nashium upload {submitted_path} --name \"My Bot\" --base-url https://nashium.com{Colors.RESET}")
        print()
        return 0
    else:
        _print_header("❌ DID NOT QUALIFY")
        _print_failure("Your bot did not achieve statistically significant wins against all opponents.")
        print()
        print("  To qualify, you need to:")
        print(
            f"    • Win more than {Colors.BOLD}51.55%{Colors.RESET} of rounds against {Colors.BOLD}each{Colors.RESET} opponent")
        print("    • This equals winning at least 5,155 out of 10,000 rounds")
        print()
        print("  Tips:")
        print("    • Study the opponent names - they hint at their strategies!")
        print("    • 'always_heads' always plays 0, 'always_tails' always plays 1")
        print("    • 'mirror' copies your last move")
        print("    • 'alternator' switches between 0 and 1 each round")
        print()
        return 2


# ============================================================================
# RUN COMMAND
# ============================================================================

def cmd_run(args: argparse.Namespace) -> int:
    a_path = Path(args.bot_a)
    b_path = Path(args.bot_b)

    if not a_path.exists():
        _print_failure(f"Bot file not found: {a_path}")
        return 1
    if not b_path.exists():
        _print_failure(f"Bot file not found: {b_path}")
        return 1

    _print_header(f"Match: {a_path.name} vs {b_path.name}")

    seed = args.seed
    if seed is None:
        seed = stable_seed(_read_bytes(a_path), _read_bytes(b_path))

    try:
        bot_a = load_bot_from_file(a_path, seed)
        bot_b = load_bot_from_file(b_path, seed)
    except Exception as e:
        _print_failure(f"Error loading bots: {e}")
        return 1

    config = MatchConfig(
        rounds=args.rounds,
        invert_opponent=args.invert_opponent,
        max_total_time_seconds_per_bot=args.time_budget,
    )

    try:
        summary = run_match(bot_a, bot_b, config)
    except Exception as e:
        _print_failure(f"Error during match: {e}")
        return 1

    status, explanation = _format_result(
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
# CHECK COMMAND (determinism)
# ============================================================================

def cmd_check(args: argparse.Namespace) -> int:
    submitted_path = Path(args.bot)

    if not submitted_path.exists():
        _print_failure(f"Bot file not found: {submitted_path}")
        return 1

    _print_header(f"Checking Determinism: {submitted_path.name}")

    _print_info("Running your bot twice with the same seed to verify identical behavior...")
    print()

    seed = args.seed
    if seed is None:
        seed = stable_seed(_read_bytes(submitted_path), b"determinism_check")

    config = MatchConfig(
        rounds=args.rounds,
        invert_opponent=args.invert_opponent,
        max_total_time_seconds_per_bot=args.time_budget,
    )

    opponents = sample_leaderboard_bots(seed)
    deterministic = True

    for name, opp in opponents:
        try:
            bot_1 = load_bot_from_file(submitted_path, seed)
            trace_1 = run_match_trace(bot_1, opp, config)

            bot_2 = load_bot_from_file(submitted_path, seed)
            trace_2 = run_match_trace(bot_2, opp, config)
        except Exception as e:
            _print_failure(f"Error testing against {name}: {e}")
            return 1

        same = trace_1.submitted_moves == trace_2.submitted_moves

        if same:
            _print_success(f"vs {name}: Deterministic ✓")
        else:
            _print_failure(f"vs {name}: NOT deterministic!")
            # Find first difference
            for i, (m1, m2) in enumerate(zip(trace_1.submitted_moves, trace_2.submitted_moves)):
                if m1 != m2:
                    _print_dim(f"First difference at round {i}: got {m1} then {m2}")
                    break
            deterministic = False

    print()

    if deterministic:
        _print_header("✓ DETERMINISTIC")
        _print_success("Your bot produces identical moves when given the same seed.")
        print()
        print("  This is required for fair competition. Your bot is ready!")
        print()
        return 0
    else:
        _print_header("✗ NOT DETERMINISTIC")
        _print_failure("Your bot produces DIFFERENT moves when run twice with the same seed!")
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
# SEED COMMAND
# ============================================================================

def cmd_seed(args: argparse.Namespace) -> int:
    a_path = Path(args.bot_a)
    b_path = Path(args.bot_b)

    if not a_path.exists():
        _print_failure(f"File not found: {a_path}")
        return 1
    if not b_path.exists():
        _print_failure(f"File not found: {b_path}")
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
        _print_failure(f"Bot file not found: {bot_path}")
        return 1

    _print_header(f"Uploading: {args.name}")

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
        _print_success("Bot uploaded successfully!")
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
        _print_failure(f"Upload failed: {e}")
        return 1


# ============================================================================
# ERROR HANDLING
# ============================================================================

def _format_user_error(e: Exception, bot_path: Path) -> str:
    """Convert a technical exception into a user-friendly message."""
    error_type = type(e).__name__
    error_msg = str(e)

    # Common Python errors with friendly explanations
    if isinstance(e, AttributeError):
        if "'RoundState'" in error_msg or "RoundState" in error_msg:
            return f"""
{Colors.RED}AttributeError in your bot code{Colors.RESET}

You're trying to access an attribute on the RoundState CLASS instead of the 'state' INSTANCE.

{Colors.YELLOW}Wrong:{Colors.RESET}  RoundState.opponent_history
{Colors.GREEN}Right:{Colors.RESET}  state.opponent_history

Remember: 
  • 'state' is the variable passed to your move() method
  • 'RoundState' is just the type hint

Check your move() method in {bot_path}
"""
        if ".last" in error_msg:
            return f"""
{Colors.RED}AttributeError: '.last' doesn't exist{Colors.RESET}

Python tuples don't have a .last attribute. Use [-1] to get the last element:

{Colors.YELLOW}Wrong:{Colors.RESET}  state.opponent_history.last
{Colors.GREEN}Right:{Colors.RESET}  state.opponent_history[-1]

Check your code in {bot_path}
"""

    if isinstance(e, IndexError):
        if "tuple index out of range" in error_msg or "list index out of range" in error_msg:
            return f"""
{Colors.RED}IndexError: Trying to access history that doesn't exist yet{Colors.RESET}

On round 0, there's no history! Check before accessing:

{Colors.YELLOW}Wrong:{Colors.RESET}
    return state.opponent_history[-1]  # Crashes on round 0!

{Colors.GREEN}Right:{Colors.RESET}
    if len(state.opponent_history) == 0:
        return 0  # Default for first round
    return state.opponent_history[-1]

Or use round_index:
    if state.round_index == 0:
        return 0

Check your code in {bot_path}
"""

    if isinstance(e, BotLoadError):
        return f"""
{Colors.RED}Failed to load your bot{Colors.RESET}

{error_msg}

Make sure your bot file:
  1. Has a create_bot(seed) function
  2. Returns an object with a move(state) method
  3. Has no syntax errors

Example structure:
    class MyBot:
        def __init__(self, seed):
            pass
        def move(self, state):
            return 0

    def create_bot(seed):
        return MyBot(seed)
"""

    if isinstance(e, InvalidMoveError):
        return f"""
{Colors.RED}Your bot returned an invalid move{Colors.RESET}

{error_msg}

Your move() method must return either 0 or 1:
  • 0 = Heads
  • 1 = Tails

Check what your move() method is returning in {bot_path}
"""

    if isinstance(e, BotTimeoutError):
        return f"""
{Colors.RED}Your bot ran out of time!{Colors.RESET}

{error_msg}

You have 100 seconds TOTAL for all 10,000 moves.
That's about 0.01 seconds (10ms) per move on average.

Tips to speed up your bot:
  • Avoid expensive operations in move()
  • Pre-compute what you can in __init__
  • Use simple data structures
  • Don't do heavy pattern matching every round
"""

    # Generic fallback
    return f"""
{Colors.RED}Error: {error_type}{Colors.RESET}

{error_msg}

This error occurred while running your bot in {bot_path}

If you're stuck, check:
  1. Your move() method returns 0 or 1
  2. You handle round 0 (empty history) correctly
  3. You're using 'state' not 'RoundState' in move()
"""


# ============================================================================
# MAIN
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nashium",
        description="Nashium SDK - Create and test your matching pennies bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  nashium scaffold my_bot.py          Create a new bot from template
  nashium qualify my_bot.py           Test against sample opponents
  nashium check my_bot.py             Verify your bot is deterministic
  nashium run bot_a.py bot_b.py       Run a match between two bots
        """
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Scaffold
    p_scaffold = sub.add_parser("scaffold", help="Create a new bot from template")
    p_scaffold.add_argument("path", help="Path for the new bot file (e.g., my_bot.py)")
    p_scaffold.set_defaults(func=cmd_scaffold)

    # Seed
    p_seed = sub.add_parser("seed", help="Calculate the seed for two bots")
    p_seed.add_argument("bot_a", help="First bot file")
    p_seed.add_argument("bot_b", help="Second bot file")
    p_seed.set_defaults(func=cmd_seed)

    # Check
    p_check = sub.add_parser("check", help="Verify your bot is deterministic")
    p_check.add_argument("bot", help="Your bot file")
    p_check.add_argument("--rounds", type=int, default=2_000, help="Rounds per test (default: 2000)")
    p_check.add_argument("--seed", type=int, default=None, help="Override the random seed")
    p_check.add_argument("--no-invert-opponent", action="store_false", dest="invert_opponent")
    p_check.set_defaults(invert_opponent=True)
    p_check.add_argument("--time-budget", type=float, default=100.0, help="Time limit in seconds")
    p_check.set_defaults(func=cmd_check)

    # Run
    p_run = sub.add_parser("run", help="Run a match between two bots")
    p_run.add_argument("bot_a", help="First bot file (treated as 'submitted')")
    p_run.add_argument("bot_b", help="Second bot file (treated as 'opponent')")
    p_run.add_argument("--rounds", type=int, default=10_000, help="Rounds to play (default: 10000)")
    p_run.add_argument("--seed", type=int, default=None, help="Override the random seed")
    p_run.add_argument("--invert-opponent", action="store_true", default=False, help="Invert opponent moves")
    p_run.add_argument("--time-budget", type=float, default=100.0, help="Time limit in seconds")
    p_run.set_defaults(func=cmd_run)

    # Qualify
    p_qualify = sub.add_parser("qualify", help="Test your bot against sample opponents")
    p_qualify.add_argument("bot", help="Your bot file")
    p_qualify.add_argument("--rounds", type=int, default=10_000, help="Rounds per match (default: 10000)")
    p_qualify.add_argument("--seed", type=int, default=None, help="Override the random seed")
    p_qualify.add_argument("--no-invert-opponent", action="store_false", dest="invert_opponent")
    p_qualify.set_defaults(invert_opponent=True)
    p_qualify.add_argument("--time-budget", type=float, default=100.0, help="Time limit in seconds")
    p_qualify.set_defaults(func=cmd_qualify)

    # Upload
    p_upload = sub.add_parser("upload", help="Upload your bot to the server")
    p_upload.add_argument("bot", help="Your bot file")
    p_upload.add_argument("--name", required=True, help="Name for your bot")
    p_upload.add_argument("--base-url", required=True, help="Server URL (e.g., https://nashium.com)")
    p_upload.add_argument("--endpoint", default="/api/bots/submit", help="API endpoint")
    p_upload.add_argument("--token", default=None, help="Your authentication token")
    p_upload.add_argument("--timeout-seconds", type=float, default=30.0)
    p_upload.set_defaults(func=cmd_upload)

    args = parser.parse_args(argv)

    try:
        return int(args.func(args))
    except (BotLoadError, InvalidMoveError, BotTimeoutError) as e:
        # Get the bot path from args if available
        bot_path = Path(getattr(args, 'bot', getattr(args, 'bot_a', 'your_bot.py')))
        print(_format_user_error(e, bot_path))
        return 2
    except Exception as e:
        bot_path = Path(getattr(args, 'bot', getattr(args, 'bot_a', 'your_bot.py')))
        print(_format_user_error(e, bot_path))
        # Also show the actual traceback in dim for debugging
        print(f"\n{Colors.DIM}Technical details:{Colors.RESET}")
        print(Colors.DIM)
        traceback.print_exc()
        print(Colors.RESET)
        return 1