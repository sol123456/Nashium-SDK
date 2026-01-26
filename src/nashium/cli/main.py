from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from .commands import cmd_check, cmd_qualify, cmd_run, cmd_scaffold
from .formatting import Colors, format_user_error
from ..core.errors import BotLoadError, BotTimeoutError, InvalidMoveError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nashium",
        description="Nashium SDK - Create and test your matching pennies bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  nashium scaffold my_bot.py            Create a new bot from template
  nashium qualify my_bot.py             Test against sample opponents  
  nashium qualify my_bot.py --sandbox   Test in sandboxed subprocess
  nashium check my_bot.py               Verify your bot is deterministic
  nashium run bot_a.py bot_b.py         Run a match between two bots
        """
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Scaffold
    p_scaffold = sub.add_parser("scaffold", help="Create a new bot from template")
    p_scaffold.add_argument("path", help="Path for the new bot file")
    p_scaffold.set_defaults(func=cmd_scaffold)

    # Check
    p_check = sub.add_parser("check", help="Verify your bot is deterministic")
    p_check.add_argument("bot", help="Your bot file")
    p_check.add_argument("--rounds", type=int, default=10_000)
    p_check.add_argument("--seed", type=int, default=None)
    p_check.add_argument("--time-budget", type=float, default=100.0)
    p_check.add_argument(
        "--memory",
        default=None,
        help="Memory limit per bot for --sandbox/--docker (e.g. 200m, 200mb, 209715200)",
    )
    p_check.add_argument("--sandbox", action="store_true", help="Run in sandboxed subprocess")
    p_check.add_argument("--docker",action="store_true", help="Run in Docker containers (full isolation, matches server)",)
    p_check.set_defaults(func=cmd_check)

    # Run
    p_run = sub.add_parser("run", help="Run a match between two bots")
    p_run.add_argument("bot_a", help="First bot file")
    p_run.add_argument("bot_b", help="Second bot file")
    p_run.add_argument("--rounds", type=int, default=10_000)
    p_run.add_argument("--seed", type=int, default=None)
    p_run.add_argument("--time-budget", type=float, default=100.0)
    p_run.add_argument(
        "--memory",
        default=None,
        help="Memory limit per bot for --sandbox/--docker (e.g. 200m, 200mb, 209715200)",
    )
    p_run.add_argument("--sandbox", action="store_true", help="Run in sandboxed subprocess")
    p_run.add_argument("--docker",action="store_true", help="Run in Docker containers (full isolation, matches server)",)
    p_run.add_argument(
        "--save-output",
        action="store_true",
        help="Save per-match submitted outputs and score (1/0) logs",
    )
    p_run.add_argument(
        "--save-output-dir",
        default="nashium_match_logs",
        help="Directory to write saved match logs",
    )
    p_run.set_defaults(func=cmd_run)

    # Qualify
    p_qualify = sub.add_parser("qualify", help="Test your bot against sample opponents")
    p_qualify.add_argument("bot", help="Your bot file")
    p_qualify.add_argument("--rounds", type=int, default=10_000)
    p_qualify.add_argument("--seed", type=int, default=None)
    p_qualify.add_argument("--time-budget", type=float, default=100.0)
    p_qualify.add_argument(
        "--memory",
        default=None,
        help="Memory limit per bot for --sandbox/--docker (e.g. 200m, 200mb, 209715200)",
    )
    p_qualify.add_argument("--sandbox", action="store_true", help="Run in sandboxed subprocess")
    p_qualify.add_argument("--docker",action="store_true", help="Run in Docker containers (full isolation, matches server)",)
    p_qualify.add_argument(
        "--save-output",
        action="store_true",
        help="Save per-match submitted outputs and score (1/0) logs",
    )
    p_qualify.add_argument(
        "--save-output-dir",
        default="nashium_match_logs",
        help="Directory to write saved match logs",
    )
    p_qualify.set_defaults(func=cmd_qualify)

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
        print(f"\n{Colors.DIM}Technical details:{Colors.RESET}")
        print(Colors.DIM)
        traceback.print_exc()
        print(Colors.RESET)
        return 1