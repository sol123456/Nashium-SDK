from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path

from .client import NashiumClient, NashiumClientConfig
from .engine import InteractionResult, MatchConfig, run_match, run_match_trace
from .errors import BotLoadError, BotTimeoutError, InvalidMoveError
from .loading import load_bot_from_file
from .sample_bots import sample_leaderboard_bots
from .util import stable_seed


def _read_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def cmd_scaffold(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.exists():
        raise SystemExit(f"Refusing to overwrite existing file: {target}")

    target.write_text(
        "from nashium import RoundState\n\n\nclass MyBot:\n    def __init__(self, seed: int):\n        self._mode = None\n\n    def move(self, state: RoundState) -> int:\n        if state.round_index == 0:\n            return 0\n        if state.round_index == 1:\n            return 1\n        if state.round_index == 2:\n            return 1\n\n        if self._mode is None and len(state.opponent_history) >= 3:\n            o0, o1, o2 = state.opponent_history[0], state.opponent_history[1], state.opponent_history[2]\n            if o0 == 0 and o1 == 0 and o2 == 0:\n                self._mode = \"const0\"\n            elif o0 == 1 and o1 == 1 and o2 == 1:\n                self._mode = \"const1\"\n            elif (o0, o1, o2) == (1, 0, 1):\n                self._mode = \"alternator\"\n            elif (o0, o1, o2) == (1, 1, 0):\n                self._mode = \"mirror\"\n            else:\n                self._mode = \"fallback\"\n\n        if self._mode == \"const0\":\n            return 0\n        if self._mode == \"const1\":\n            return 1\n        if self._mode == \"alternator\":\n            return 1 if (state.round_index % 2 == 0) else 0\n        if self._mode == \"mirror\":\n            return 1 - state.my_history[-1]\n\n        return 0\n\n\ndef create_bot(seed: int):\n    return MyBot(seed)\n",
        encoding="utf-8",
    )
    print(f"Wrote {target}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    a_path = Path(args.bot_a)
    b_path = Path(args.bot_b)
    seed = stable_seed(_read_bytes(a_path), _read_bytes(b_path))
    print(seed)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    a_path = Path(args.bot_a)
    b_path = Path(args.bot_b)

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

    print(f"rounds={summary.rounds}")
    print(f"submitted_wins={summary.submitted_wins}")
    print(f"submitted_win_rate={summary.submitted_win_rate:.4f}")
    print(f"result={summary.result}")
    print(f"stat_sig={summary.stat_sig}")
    print(f"submitted_time_seconds={summary.submitted_time_seconds:.6f}")
    print(f"leaderboard_time_seconds={summary.leaderboard_time_seconds:.6f}")
    print(f"wall_time_seconds={summary.wall_time_seconds:.3f}")
    return 0


def cmd_qualify(args: argparse.Namespace) -> int:
    submitted_path = Path(args.bot)

    seed = args.seed
    if seed is None:
        seed = stable_seed(_read_bytes(submitted_path), b"sample_leaderboard")

    opponents = sample_leaderboard_bots(seed)

    config = MatchConfig(
        rounds=args.rounds,
        invert_opponent=args.invert_opponent,
        max_total_time_seconds_per_bot=args.time_budget,
    )

    all_pass = True
    for name, opp in opponents:
        submitted = load_bot_from_file(submitted_path, seed)
        summary = run_match(submitted, opp, config)
        print(f"opponent={name}")
        print(f"  submitted_wins={summary.submitted_wins}")
        print(f"  submitted_win_rate={summary.submitted_win_rate:.4f}")
        print(f"  result={summary.result}")
        print(f"  stat_sig={summary.stat_sig}")
        if not (summary.result == InteractionResult.S_WIN and summary.stat_sig):
            all_pass = False

    print(f"qualified={all_pass}")
    return 0 if all_pass else 2


def cmd_check(args: argparse.Namespace) -> int:
    submitted_path = Path(args.bot)

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
        bot_1 = load_bot_from_file(submitted_path, seed)
        trace_1 = run_match_trace(bot_1, opp, config)

        bot_2 = load_bot_from_file(submitted_path, seed)
        trace_2 = run_match_trace(bot_2, opp, config)

        same = trace_1.submitted_moves == trace_2.submitted_moves
        print(f"opponent={name} deterministic={same}")
        if not same:
            deterministic = False

    print(f"deterministic={deterministic}")
    return 0 if deterministic else 3


def cmd_upload(args: argparse.Namespace) -> int:
    bot_path = Path(args.bot)
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
        "creationTime": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
    }

    resp = client.request_json("POST", args.endpoint, payload=payload)
    print(resp)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nashium")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scaffold = sub.add_parser("scaffold")
    p_scaffold.add_argument("path")
    p_scaffold.set_defaults(func=cmd_scaffold)

    p_seed = sub.add_parser("seed")
    p_seed.add_argument("bot_a")
    p_seed.add_argument("bot_b")
    p_seed.set_defaults(func=cmd_seed)

    p_check = sub.add_parser("check")
    p_check.add_argument("bot")
    p_check.add_argument("--rounds", type=int, default=2_000)
    p_check.add_argument("--seed", type=int, default=None)
    p_check.add_argument("--no-invert-opponent", action="store_false", dest="invert_opponent")
    p_check.set_defaults(invert_opponent=True)
    p_check.add_argument("--time-budget", type=float, default=100.0)
    p_check.set_defaults(func=cmd_check)

    p_run = sub.add_parser("run")
    p_run.add_argument("bot_a")
    p_run.add_argument("bot_b")
    p_run.add_argument("--rounds", type=int, default=10_000)
    p_run.add_argument("--seed", type=int, default=None)
    p_run.add_argument("--invert-opponent", action="store_true", default=False)
    p_run.add_argument("--time-budget", type=float, default=100.0)
    p_run.set_defaults(func=cmd_run)

    p_qualify = sub.add_parser("qualify")
    p_qualify.add_argument("bot")
    p_qualify.add_argument("--rounds", type=int, default=10_000)
    p_qualify.add_argument("--seed", type=int, default=None)
    p_qualify.add_argument("--no-invert-opponent", action="store_false", dest="invert_opponent")
    p_qualify.set_defaults(invert_opponent=True)
    p_qualify.add_argument("--time-budget", type=float, default=100.0)
    p_qualify.set_defaults(func=cmd_qualify)

    p_upload = sub.add_parser("upload")
    p_upload.add_argument("bot")
    p_upload.add_argument("--name", required=True)
    p_upload.add_argument("--base-url", required=True)
    p_upload.add_argument("--endpoint", default="/api/bots")
    p_upload.add_argument("--token", default=None)
    p_upload.add_argument("--timeout-seconds", type=float, default=30.0)
    p_upload.set_defaults(func=cmd_upload)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (BotLoadError, InvalidMoveError, BotTimeoutError) as e:
        print(str(e))
        return 2
