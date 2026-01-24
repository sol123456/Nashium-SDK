from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core import InteractionResult


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


def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'═' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}  {text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'═' * 60}{Colors.RESET}\n")


def print_success(text: str):
    print(f"{Colors.GREEN}✓ {text}{Colors.RESET}")


def print_failure(text: str):
    print(f"{Colors.RED}✗ {text}{Colors.RESET}")


def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.RESET}")


def print_info(text: str):
    print(f"{Colors.BLUE}ℹ {text}{Colors.RESET}")


def print_dim(text: str):
    print(f"{Colors.DIM}  {text}{Colors.RESET}")


def format_time_warning(max_time: float, budget: float = 100.0) -> str:
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


def format_result(result: "InteractionResult", stat_sig: bool, wins: int, rounds: int) -> tuple[str, str]:
    """Return (short_status, explanation)."""
    from ..core import InteractionResult

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


def extract_error_location(e: Exception, bot_path: Path) -> tuple[str | None, int | None, str | None]:
    """
    Extract the filename, line number, and line text from an exception
    that occurred in the user's bot code.

    Returns (filename, lineno, line_text) or (None, None, None) if not found.
    """
    tb = traceback.extract_tb(e.__traceback__)

    # Look for frames in the user's bot file
    bot_path_str = str(bot_path.resolve())

    for frame in reversed(tb):
        # Check if this frame is from the user's bot file
        if bot_path_str in frame.filename or bot_path.name in frame.filename:
            return (frame.filename, frame.lineno, frame.line)

    # Also check for dynamically loaded modules (they have the bot name in them)
    for frame in reversed(tb):
        if "nashium_userbot_" in frame.filename or frame.filename.endswith(bot_path.name):
            return (frame.filename, frame.lineno, frame.line)

    # If we can't find the bot file, return the last frame (most recent)
    if tb:
        last_frame = tb[-1]
        return (last_frame.filename, last_frame.lineno, last_frame.line)

    return (None, None, None)


def format_user_error(e: Exception, bot_path: Path) -> str:
    """Convert a technical exception into a user-friendly message with location info."""
    from ..core.errors import BotLoadError, BotTimeoutError, InvalidMoveError

    error_type = type(e).__name__
    error_msg = str(e)

    # Extract location info
    filename, lineno, line_text = extract_error_location(e, bot_path)

    # Build location string
    location_str = ""
    if lineno is not None:
        location_str = f"\n{Colors.YELLOW}Location:{Colors.RESET} Line {lineno}"
        if line_text:
            location_str += f"\n{Colors.DIM}    {line_text.strip()}{Colors.RESET}"
        location_str += f"\n{Colors.DIM}    in {bot_path}{Colors.RESET}\n"

    # Common Python errors with friendly explanations
    if isinstance(e, AttributeError):
        if "'RoundState'" in error_msg or "RoundState" in error_msg:
            return f"""
{Colors.RED}AttributeError in your bot code{Colors.RESET}
{location_str}
You're trying to access an attribute on the RoundState CLASS instead of the 'state' INSTANCE.

{Colors.YELLOW}Wrong:{Colors.RESET}  RoundState.opponent_history
{Colors.GREEN}Right:{Colors.RESET}  state.opponent_history

Remember: 
  • 'state' is the variable passed to your move() method
  • 'RoundState' is just the type hint
"""
        if ".last" in error_msg:
            return f"""
{Colors.RED}AttributeError: '.last' doesn't exist{Colors.RESET}
{location_str}
Python tuples don't have a .last attribute. Use [-1] to get the last element:

{Colors.YELLOW}Wrong:{Colors.RESET}  state.opponent_history.last
{Colors.GREEN}Right:{Colors.RESET}  state.opponent_history[-1]
"""
        # Generic AttributeError
        return f"""
{Colors.RED}AttributeError: {error_msg}{Colors.RESET}
{location_str}
You're trying to access an attribute that doesn't exist.
Check the spelling and make sure you're using the right variable.
"""

    if isinstance(e, IndexError):
        return f"""
{Colors.RED}IndexError: {error_msg}{Colors.RESET}
{location_str}
You're trying to access an index that doesn't exist.

On round 0, there's no history yet! Always check before accessing:

{Colors.YELLOW}Wrong:{Colors.RESET}
    return state.opponent_history[-1]  # Crashes on round 0!

{Colors.GREEN}Right:{Colors.RESET}
    if len(state.opponent_history) == 0:
        return 0  # Default for first round
    return state.opponent_history[-1]

Or use round_index:
    if state.round_index == 0:
        return 0
"""

    if isinstance(e, TypeError):
        return f"""
{Colors.RED}TypeError: {error_msg}{Colors.RESET}
{location_str}
You're using a value of the wrong type. Common causes:
  • Doing math on None
  • Calling something that isn't a function
  • Passing wrong number of arguments
"""

    if isinstance(e, NameError):
        return f"""
{Colors.RED}NameError: {error_msg}{Colors.RESET}
{location_str}
You're using a variable that doesn't exist. Check for:
  • Typos in variable names
  • Using a variable before defining it
  • Forgetting to import something
"""

    if isinstance(e, BotLoadError):
        return f"""
{Colors.RED}Failed to load your bot{Colors.RESET}

{error_msg}

Make sure your bot file:
  1. Has no syntax errors
  2. Has a create_bot(seed) function
  3. Returns an object with a move(state) method

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
{location_str}
{error_msg}

Your move() method must return either 0 or 1:
  • 0 = Heads
  • 1 = Tails

Check what your move() method is returning.
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

    if isinstance(e, SyntaxError):
        # SyntaxError has its own location info
        return f"""
{Colors.RED}SyntaxError in your bot code{Colors.RESET}

{Colors.YELLOW}Location:{Colors.RESET} Line {e.lineno}
{Colors.DIM}    {e.text.strip() if e.text else ''}{Colors.RESET}
{Colors.DIM}    in {e.filename or bot_path}{Colors.RESET}

{error_msg}

Check for:
  • Missing colons after if/for/def/class
  • Mismatched parentheses or brackets
  • Invalid Python syntax
"""

    # Generic fallback
    return f"""
{Colors.RED}Error: {error_type}{Colors.RESET}
{location_str}
{error_msg}

If you're stuck, check:
  1. Your move() method returns 0 or 1
  2. You handle round 0 (empty history) correctly
  3. You're using 'state' not 'RoundState' in move()
"""