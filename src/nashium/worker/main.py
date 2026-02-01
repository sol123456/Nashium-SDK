"""
CLI entry point for running the worker.

Usage:
    python -m nashium_sdk.worker --api-url http://localhost:8080 --token YOUR_TOKEN

Or with environment variables:
    NASHIUM_API_URL=http://localhost:8080 NASHIUM_WORKER_TOKEN=xxx python -m nashium_sdk.worker
"""

from __future__ import annotations

import argparse
import os
import sys


def get_wsl_host_ip() -> str:
    """
    Get the Windows host IP from within WSL2.

    Returns the IP that can be used to reach Windows from WSL2.
    """
    try:
        # Try reading from /etc/resolv.conf (WSL2 sets this to the host)
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                if line.startswith("nameserver"):
                    return line.split()[1]
    except Exception:
        pass

    # Fallback - try common WSL2 gateway
    return "172.17.0.1"


def main():
    parser = argparse.ArgumentParser(
        description="Nashium Match Worker - Executes matches for the competition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  NASHIUM_API_URL        Base URL of the JHipster server
  NASHIUM_WORKER_TOKEN   Authentication token for the worker API
  NASHIUM_SANDBOX        Enable/disable sandbox (true/false)
  NASHIUM_DOCKER         Use Docker for sandboxing (true/false)

WSL2 Note:
  If running in WSL2 and the JHipster server is on Windows, you may need to
  use the Windows host IP. Try: --api-url http://$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}'):8080
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

    parser.add_argument(
        "--sandbox/--no-sandbox",
        dest="sandbox",
        default=os.environ.get("NASHIUM_SANDBOX", "true").lower() == "true",
        help="Enable sandboxed execution (default: enabled)",
    )

    parser.add_argument(
        "--docker/--no-docker",
        dest="docker",
        default=os.environ.get("NASHIUM_DOCKER", "false").lower() == "true",
        help="Use Docker for sandboxing (default: disabled)",
    )

    parser.add_argument(
        "--rounds",
        type=int,
        default=int(os.environ.get("NASHIUM_ROUNDS", "10000")),
        help="Number of rounds per match (default: 10000)",
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

    # Validate token
    if not args.token:
        print("Error: Worker token is required")
        print("Provide via --token or NASHIUM_WORKER_TOKEN environment variable")
        sys.exit(1)

    # Import here to avoid circular imports and speed up --help
    from .runner import Worker, WorkerConfig

    config = WorkerConfig(
        api_base_url=api_url,
        worker_token=args.token,
        sandbox=args.sandbox,
        docker=args.docker,
        rounds=args.rounds,
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
            sys.exit(1)
    else:
        # Normal continuous operation
        worker.run_forever()


if __name__ == "__main__":
    main()