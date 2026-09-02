"""
CLI entry point for the match worker.

    python -m nashium.worker --api-url http://localhost:8080 --token YOUR_TOKEN

Environment variables:
    NASHIUM_API_URL, NASHIUM_WORKER_TOKEN, NASHIUM_ROUNDS,
    NASHIUM_MAX_TIME, NASHIUM_MAX_WALL, NASHIUM_MAX_MEMORY_MB,
    NASHIUM_IDLE_INTERVAL, NASHIUM_LOG_LEVEL
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys


def get_wsl_host_ip() -> str | None:
    """Windows host IP as seen from WSL2 (the default-gateway address)."""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        match = re.search(r"via\s+([\d.]+)", result.stdout)
        if match:
            return match.group(1)
    except Exception:  # noqa: BLE001
        pass
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nashium-worker",
        description="Nashium match worker - runs matches in Docker on behalf of the server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Requires Docker and the runner image:\n"
            "  docker build -t nashium-runner:latest src/nashium/server/\n"
        ),
    )
    p.add_argument("--api-url",
                   default=os.environ.get("NASHIUM_API_URL", "http://localhost:8080"))
    p.add_argument("--token", default=os.environ.get("NASHIUM_WORKER_TOKEN", ""))
    p.add_argument("--rounds", type=int,
                   default=int(os.environ.get("NASHIUM_ROUNDS", "10000")))
    p.add_argument("--max-time", type=float,
                   default=float(os.environ.get("NASHIUM_MAX_TIME", "100.0")),
                   help="CPU seconds per bot per match")
    p.add_argument("--max-wall", type=float,
                   default=float(os.environ.get("NASHIUM_MAX_WALL", "300.0")),
                   help="Wall-clock hang guard per bot per match")
    p.add_argument("--max-memory-mb", type=int,
                   default=int(os.environ.get("NASHIUM_MAX_MEMORY_MB", "200")))
    p.add_argument("--idle-interval", type=float,
                   default=float(os.environ.get("NASHIUM_IDLE_INTERVAL", "10.0")))
    p.add_argument("--log-level",
                   default=os.environ.get("NASHIUM_LOG_LEVEL", "INFO"),
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--wsl-host", action="store_true",
                   help="Resolve the API host to the WSL2 default gateway")
    p.add_argument("--single", action="store_true",
                   help="Claim and run at most one match, then exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    api_url = args.api_url
    if args.wsl_host:
        host_ip = get_wsl_host_ip()
        if not host_ip:
            print("Error: could not determine the WSL2 host IP "
                  "(is 'ip route' available?). Pass --api-url explicitly.",
                  file=sys.stderr)
            return 1
        tail = api_url.split("//")[-1]
        port = tail.rsplit(":", 1)[-1].rstrip("/") if ":" in tail else "8080"
        api_url = f"http://{host_ip}:{port}"
        print(f"Using WSL2 host IP: {api_url}")

    if not args.token:
        print("Error: worker token is required "
              "(--token or NASHIUM_WORKER_TOKEN)", file=sys.stderr)
        return 1

    from .runner import Worker, WorkerConfig

    worker = Worker(WorkerConfig(
        api_base_url=api_url,
        worker_token=args.token,
        rounds=args.rounds,
        max_time_per_bot=args.max_time,
        max_wall_per_bot=args.max_wall,
        max_memory_per_bot=args.max_memory_mb * 1024 * 1024,
        idle_poll_interval_seconds=args.idle_interval,
        log_level=args.log_level,
    ))

    if args.single:
        from ..server.docker import verify_environment
        verify_environment()
        return 0 if worker.run_single() else 1

    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))