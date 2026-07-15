"""
CLI entry point for running the worker.

Usage:
    python -m nashium_sdk.worker --api-url http://localhost:8080 --token YOUR_TOKEN --seed-secret YOUR_SECRET

Or with environment variables:
    NASHIUM_API_URL=http://localhost:8080 \
    NASHIUM_WORKER_TOKEN=xxx \
    NASHIUM_SEED_SECRET=yyy \
    python -m nashium_sdk.worker
"""


from __future__ import annotations

import argparse
import os
import sys
import subprocess
import re


def get_wsl_host_ip() -> str:
    """
    Get the Windows host IP by looking at the WSL default gateway.
    """
    try:
        # Run 'ip route show default'
        # Output looks like: "default via 172.31.96.1 dev eth0..."
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True
        )

        # Regex to grab the IP after the word 'via'
        match = re.search(r"via\s+([\d\.]+)", result.stdout)
        if match:
            return match.group(1)

    except Exception:
        pass

    # Fallback to the External IP you saw in logs (as a last resort)
    return "192.168.0.184"


def main():
    parser = argparse.ArgumentParser(
        description="Nashium Match Worker - Executes matches for the competition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  NASHIUM_API_URL        Base URL of the JHipster server
  NASHIUM_WORKER_TOKEN   Authentication token for the worker API
  NASHIUM_SEED_SECRET    Secret key for deterministic seed generation (REQUIRED)

Execution Mode:
  The worker uses Docker for sandboxed execution. Make sure Docker is installed
  and the Docker daemon is running.

WSL2 Note:
  If running in WSL2 and the JHipster server is on Windows, use --wsl-host flag
  to auto-detect the Windows host IP.

Example:
  python -m nashium_sdk.worker --wsl-host --token "xxx" --seed-secret "yyy"
        """,
    )

    parser.add_argument(
        "--api-url",
        default=os.environ.get("NASHIUM_API_URL", "http://localhost:8080"),
        help="Base URL of the JHipster server (default: http://localhost:8080)",
    )

    parser.add_argument(
        "--token",
        default=os.environ.get("NASHIUM_WORKER_TOKEN", ""),
        help="Worker authentication token (required)",
    )

    # parser.add_argument(
    #     "--seed-secret",
    #     default=os.environ.get("NASHIUM_SEED_SECRET", ""),
    #     help="Secret key for seed generation (required)",
    # )

    parser.add_argument(
        "--rounds",
        type=int,
        default=int(os.environ.get("NASHIUM_ROUNDS", "10000")),
        help="Number of rounds per match (default: 10000)",
    )

    parser.add_argument(
        "--max-time",
        type=float,
        default=float(os.environ.get("NASHIUM_MAX_TIME", "100.0")),
        help="Max CPU time per bot in seconds (default: 100.0)",
    )

    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("NASHIUM_POLL_INTERVAL", "2.0")),
        help="Seconds between polls when work is available (default: 2.0)",
    )

    parser.add_argument(
        "--idle-interval",
        type=float,
        default=float(os.environ.get("NASHIUM_IDLE_INTERVAL", "10.0")),
        help="Seconds between polls when idle (default: 10.0)",
    )

    parser.add_argument(
        "--log-level",
        default=os.environ.get("NASHIUM_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    parser.add_argument(
        "--wsl-host",
        action="store_true",
        help="Auto-detect WSL2 host IP and use it for API URL",
    )

    parser.add_argument(
        "--single",
        action="store_true",
        help="Run a single match and exit (for testing)",
    )

    args = parser.parse_args()

    # Handle WSL host detection
    api_url = args.api_url
    if args.wsl_host:
        host_ip = get_wsl_host_ip()
        # Extract port from existing URL
        if ":" in api_url.split("//")[-1]:
            port = api_url.split(":")[-1].rstrip("/")
        else:
            port = "8080"
        api_url = f"http://{host_ip}:{port}"
        print(f"Using WSL2 host IP: {api_url}")

    # Validate required arguments
    if not args.token:
        print("Error: Worker token is required")
        print("Provide via --token or NASHIUM_WORKER_TOKEN environment variable")
        sys.exit(1)

    # if not args.seed_secret:
    #     print("Error: Seed secret is required")
    #     print("Provide via --seed-secret or NASHIUM_SEED_SECRET environment variable")
    #     sys.exit(1)

    # Import here to avoid circular imports and speed up --help
    from .runner import Worker, WorkerConfig

    config = WorkerConfig(
        api_base_url=api_url,
        worker_token=args.token,
        # seed_secret=args.seed_secret,
        rounds=args.rounds,
        max_time_per_bot=args.max_time,
        poll_interval_seconds=args.poll_interval,
        idle_poll_interval_seconds=args.idle_interval,
        log_level=args.log_level,
    )

    worker = Worker(config)

    if args.single:
        # Single execution mode for testing
        try:
            did_work = worker.run_single()
            sys.exit(0 if did_work else 1)
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        # Normal continuous operation
        worker.run_forever()


# if __name__ == "__main__":
#     main()