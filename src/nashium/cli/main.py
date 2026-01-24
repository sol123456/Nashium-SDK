from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from .commands import cmd_check, cmd_qualify, cmd_run, cmd_scaffold, cmd_seed, cmd_upload
from .formatting import Colors, format_user_error
from ..core.errors import BotLoadError, BotTimeoutError, InvalidMoveError


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
    except (BotLoadError, InvalidMoveError, BotTimeoutError, SyntaxError) as e:
        bot_path = Path(getattr(args, 'bot', getattr(args, 'bot_a', 'your_bot.py')))
        print(format_user_error(e, bot_path))
        return 2
    except Exception as e:
        bot_path = Path(getattr(args, 'bot', getattr(args, 'bot_a', 'your_bot.py')))
        print(format_user_error(e, bot_path))
        # Show technical details in dim
        print(f"\n{Colors.DIM}Technical details:{Colors.RESET}")
        print(Colors.DIM)
        traceback.print_exc()
        print(Colors.RESET)
        return 1