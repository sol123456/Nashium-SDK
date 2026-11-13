"""``nashium farm`` - run several Nashium workers from one terminal.

    nashium farm --workers 6

That is the whole command once ``~/.nashium/farm.ini`` exists (create it with
``nashium farm --save-config``). Every other flag overrides that file.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
from typing import Optional, Sequence

from . import preflight
from .config import ConfigError, FarmConfig, default_config_path, resolve, write_config
from .display import DIM, RESET, Console

EXIT_OK = 0
EXIT_PREFLIGHT = 2
EXIT_CONFIG = 3
EXIT_FATAL = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nashium farm",
        description=(
            "Run a pool of Nashium match workers in one terminal: one prefixed "
            "console stream, one log file per worker, restart on crash, and a "
            "graceful drain on Ctrl-C."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  nashium farm --save-config --token TOKEN --seed-secret SECRET
                                                   remember settings, then...
  nashium farm --workers 6                         run 6 workers
  nashium farm --workers 4 --token-file tok.txt    token from a file instead
  nashium farm --build-image --check               build runner image, verify setup
  nashium farm --check                             re-verify an existing setup

worker count:
  Each worker runs one match at a time and a match is two containers capped at
  0.5 CPU each, so one worker ~= one core. Going above your core count is not
  free: the 100s per-bot budget is measured in wall-clock time, so oversubscribed
  bots get marked as timed out and the server stores worse results for them.

configuration (highest priority first):
  command line flags
  environment: NASHIUM_API_URL, NASHIUM_WORKER_TOKEN, NASHIUM_SEED_SECRET,
               NASHIUM_WORKERS, NASHIUM_LOG_DIR, NASHIUM_LOG_LEVEL, ...
  {config_path}
""".format(config_path=default_config_path()),
    )

    parser.add_argument("-w", "--workers", type=int, default=None,
                        help="number of workers (default: one per CPU core)")
    parser.add_argument("--api-url", default=None,
                        help="base URL of the Nashium server (default: http://localhost:8080)")
    parser.add_argument("--token", default=None, help="worker token (or NASHIUM_WORKER_TOKEN)")
    parser.add_argument("--token-file", default=None, help="read the worker token from this file")
    parser.add_argument("--seed-secret", default=None,
                        help="seed secret shared with the server (or NASHIUM_SEED_SECRET)")
    parser.add_argument("--seed-secret-file", default=None,
                        help="read the seed secret from this file")
    parser.add_argument("--wsl-host", action="store_true",
                        help="workers auto-detect the Windows host IP (WSL2 setups)")

    parser.add_argument("--rounds", type=int, default=None, help="rounds per match (default: 10000)")
    parser.add_argument("--max-time", type=float, default=None,
                        help="wall-clock budget per bot in seconds (default: 100)")
    parser.add_argument("--poll-interval", type=float, default=None, help="seconds between polls when busy")
    parser.add_argument("--idle-interval", type=float, default=None, help="seconds between polls when idle")
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    parser.add_argument("--log-dir", default=None,
                        help="directory for per-worker log files (default: logs)")
    parser.add_argument("--no-logs", action="store_true", help="do not write log files")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="hide individual worker lines, show a status table instead")

    color_group = parser.add_mutually_exclusive_group()
    color_group.add_argument("--color", dest="color", action="store_true", default=None)
    color_group.add_argument("--no-color", dest="color", action="store_false")

    parser.add_argument("--no-restart", action="store_true",
                        help="do not restart workers that exit unexpectedly")
    parser.add_argument("--shutdown-timeout", type=float, default=None,
                        help="seconds to wait for in-flight matches on Ctrl-C (default: 240)")

    parser.add_argument("--build-image", action="store_true",
                        help="build the nashium-runner:latest image before starting")
    parser.add_argument("--check", action="store_true",
                        help="run the pre-flight checks and exit without starting workers")
    parser.add_argument("--skip-health-check", action="store_true",
                        help="do not ask the server whether it is reachable")
    parser.add_argument("--ignore-cpu-check", action="store_true",
                        help="allow more workers than CPU cores without complaint")
    parser.add_argument("--clean-orphans", action="store_true",
                        help="remove leftover nashium-runner containers before starting")

    parser.add_argument("--config", default=None,
                        help=f"config file to read (default: {default_config_path()})")
    parser.add_argument("--save-config", action="store_true",
                        help="write the resolved settings (including secrets) to the config file")
    return parser


def _missing_credentials(config: FarmConfig) -> list[str]:
    missing = []
    if not config.token:
        missing.append("worker token (--token / --token-file / NASHIUM_WORKER_TOKEN)")
    if not config.seed_secret:
        missing.append("seed secret (--seed-secret / --seed-secret-file / NASHIUM_SEED_SECRET)")
    return missing


def _print_header(console: Console, config: FarmConfig) -> None:
    cores = os.cpu_count() or 1
    console.info("")
    console.info(f"  {config.workers} worker(s) on {cores} logical core(s)")
    console.info(f"  server   {config.api_url}")
    if config.log_dir:
        console.info(f"  logs     {config.log_dir}")
    console.info(f"  {DIM}Ctrl-C finishes the in-flight matches, then stops{RESET}")
    console.info("")


def run_checks(console: Console, config: FarmConfig) -> bool:
    """Run the pre-flight suite. Returns True when it is safe to start."""
    fatal_failures = 0

    # -- Python side of the worker -------------------------------------------
    missing_modules = [
        name for name in ("requests", "pydantic", "docker")
        if importlib.util.find_spec(name) is None
    ]
    if missing_modules:
        fatal_failures += 1
        console.fail(f"missing Python package(s): {', '.join(missing_modules)}")
        console.info('         fix: pip install -e ".[worker]"')
    else:
        console.ok("worker dependencies installed (requests, pydantic, docker)")

    # -- credentials ---------------------------------------------------------
    for item in _missing_credentials(config):
        fatal_failures += 1
        console.fail(f"missing {item}")
    if not _missing_credentials(config):
        console.ok("worker token and seed secret provided")

    # -- docker --------------------------------------------------------------
    docker_result = preflight.check_docker()
    (console.ok if docker_result.ok else console.fail)(docker_result.message)
    if not docker_result.ok:
        fatal_failures += 1
        if docker_result.fix:
            console.info(f"         {docker_result.fix}")

    image_result = preflight.check_image()
    if not image_result.ok and config.build_image and docker_result.ok:
        image_result = preflight.build_image(console)
    (console.ok if image_result.ok else console.fail)(image_result.message)
    if not image_result.ok:
        fatal_failures += 1
        if image_result.fix:
            console.info(f"         {image_result.fix}")

    # -- server --------------------------------------------------------------
    if config.skip_health_check:
        console.info("         server health check skipped")
    elif not config.token:
        console.info("         server health check skipped (no token)")
    else:
        server_result = preflight.check_server(config.api_url, config.token)
        (console.ok if server_result.ok else console.fail)(server_result.message)
        if not server_result.ok:
            fatal_failures += 1
            if server_result.fix:
                console.info(f"         {server_result.fix}")

    # -- capacity (advisory only) -------------------------------------------
    if not config.ignore_cpu_check:
        for advice in preflight.cpu_and_memory_advice(config.workers):
            console.warn(advice.message)
            if advice.fix:
                console.info(f"         {advice.fix}")

    return fatal_failures == 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = resolve(args)
    except ConfigError as exc:
        Console().fail(str(exc))
        return EXIT_CONFIG

    console = Console(quiet=args.quiet, color=args.color)

    if config.clean_orphans:
        removed = preflight.remove_orphan_containers(console)
        if removed:
            console.ok(f"removed {removed} leftover container(s)")

    if config.save_config:
        missing = _missing_credentials(config)
        if missing:
            for item in missing:
                console.fail(f"cannot save config - missing {item}")
            return EXIT_CONFIG
        path = write_config(config)
        console.ok(f"saved settings to {path}")
        if os.name == "posix":
            console.info("         file is chmod 600 - it holds your token")
        if not config.check_only:
            console.info(f"         start workers with: nashium farm --workers {config.workers}")
            return EXIT_OK

    if config.check_only:
        console.info("")
        healthy = run_checks(console, config)
        console.info("")
        if healthy:
            console.ok(f"ready - start with: nashium farm --workers {config.workers}")
            return EXIT_OK
        console.fail("pre-flight checks failed, see the messages above")
        return EXIT_PREFLIGHT

    missing = _missing_credentials(config)
    if missing:
        for item in missing:
            console.fail(f"missing {item}")
        console.info("         store them once with: nashium farm --save-config --token ... --seed-secret ...")
        return EXIT_CONFIG

    _print_header(console, config)
    if not run_checks(console, config):
        console.fail("pre-flight checks failed - fix the items above and try again")
        return EXIT_PREFLIGHT

    from .supervisor import Supervisor

    code = Supervisor(config, console).run()
    return EXIT_FATAL if code == 3 else code


def cmd_farm(args) -> int:
    """Entry point used by the ``nashium farm`` subcommand delegation."""
    return main(getattr(args, "farm_args", None) or ["--help"])


if __name__ == "__main__":
    raise SystemExit(main())
