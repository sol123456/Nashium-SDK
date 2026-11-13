#!/usr/bin/env python3
"""One-shot setup + multi-worker launcher (with per-worker tabs & safe shutdown).

Run inside WSL2 (on Windows), Linux, or macOS from the repo root:

    python3 scripts/bootstrap.py --token YOUR_TOKEN --workers 6

What this does:
  1. Creates .venv if missing
  2. Installs nashium-sdk (including docker, requests, pydantic) into .venv
  3. Builds the nashium-runner:latest sandbox Docker image
  4. Starts N worker processes in an interactive tabbed console:
       - Press 1-9 / Tab / Left / Right to switch worker tabs (0 for All)
       - Press Ctrl+C for a safe 120s drain (lets in-flight matches finish)
       - Type /kill + Enter to instantly force-kill all workers and reap containers

Do NOT put workers inside Docker. Each worker runs on the host/WSL2 and talks
to Docker to sandbox individual bot matches.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
VENV = ROOT / ".venv"
DOCKERFILE_DIR = SRC / "nashium" / "server"
IMAGE = "nashium-runner:latest"
DEFAULT_API_URL = "https://nashium.com"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def venv_nashium() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "nashium-worker.exe"
    return VENV / "bin" / "nashium-worker"


def run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, **kwargs)


def ensure_platform() -> None:
    if os.name == "nt":
        sys.exit(
            "Native Windows (PowerShell / cmd) is not supported because Linux Docker\n"
            "containers cannot connect to Windows host AF_UNIX sockets.\n\n"
            "Open a WSL2 tab in Windows Terminal (e.g. Ubuntu) and run:\n"
            "  python3 scripts/bootstrap.py --token YOUR_TOKEN --workers 6"
        )


def ensure_python() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Need Python 3.10+.")


def ensure_docker() -> None:
    if not shutil.which("docker"):
        sys.exit(
            "Docker is not on PATH.\n"
            "Install Docker Desktop (and enable WSL2 integration on Windows):\n"
            "  https://docs.docker.com/get-docker/\n"
            "Then start Docker and re-run this script."
        )
    try:
        subprocess.check_call(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        sys.exit(
            "Docker is installed but the daemon is not reachable.\n"
            "Start Docker Desktop (and verify Settings -> Resources -> WSL Integration)."
        )


def ensure_venv() -> Path:
    py = venv_python()
    if not py.exists():
        print(f"Creating venv at {VENV}")
        run([sys.executable, "-m", "venv", str(VENV)])
    return py


def install(py: Path) -> None:
    run([str(py), "-m", "pip", "install", "-U", "pip"])
    run([str(py), "-m", "pip", "install", "-e", str(ROOT)])


def image_exists() -> bool:
    return (
        subprocess.call(
            ["docker", "image", "inspect", IMAGE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        == 0
    )


def build_image() -> None:
    run(
        [
            "docker",
            "build",
            "-t",
            IMAGE,
            "-f",
            str(DOCKERFILE_DIR / "Dockerfile"),
            str(DOCKERFILE_DIR),
        ]
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Set up Nashium worker and optionally run N copies in individual tabs"
    )
    p.add_argument(
        "--token",
        default=os.environ.get("NASHIUM_WORKER_TOKEN", ""),
        help="Worker API token (or set NASHIUM_WORKER_TOKEN)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="How many workers to start (default: 1; >=2 opens tabbed console)",
    )
    p.add_argument(
        "--api-url",
        default=os.environ.get("NASHIUM_API_URL") or DEFAULT_API_URL,
        help=f"Base URL of the Nashium server (default: {DEFAULT_API_URL})",
    )
    p.add_argument(
        "--shutdown-timeout",
        type=float,
        default=120.0,
        help="Seconds to let workers finish in-flight matches on Ctrl+C before force-kill (default: 120)",
    )
    p.add_argument(
        "--log-level",
        default=os.environ.get("NASHIUM_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    p.add_argument(
        "--setup-only",
        action="store_true",
        help="Install venv + build Docker image, do not start workers",
    )
    p.add_argument(
        "--skip-docker-build",
        action="store_true",
        help="Skip building nashium-runner:latest",
    )
    p.add_argument(
        "--fast",
        action="store_true",
        help="Skip pip install and docker build if .venv and runner image already exist",
    )
    ui = p.add_mutually_exclusive_group()
    ui.add_argument(
        "--tabs",
        action="store_true",
        help="Force the interactive per-worker tabbed console (even for 1 worker)",
    )
    ui.add_argument(
        "--plain",
        action="store_true",
        help="Stream combined [W1]..[WN] logs instead of the tabbed console",
    )
    ui.add_argument(
        "--wt-tabs",
        action="store_true",
        help="Spawn separate native Windows Terminal WSL2 tabs via wt.exe",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    os.chdir(ROOT)
    ensure_platform()
    ensure_python()
    ensure_docker()

    if args.workers < 1:
        sys.exit("--workers must be >= 1")

    worker_bin = venv_nashium()
    already_ready = worker_bin.exists() and image_exists()

    if not (args.fast and already_ready):
        py = ensure_venv()
        install(py)
        if not args.skip_docker_build:
            build_image()

    if args.setup_only:
        print("Setup done. Start workers later with:")
        print(f"  python3 scripts/bootstrap.py --fast --token YOUR_TOKEN --workers 6")
        print("or:")
        print(f"  {worker_bin} --token YOUR_TOKEN --workers 6")
        return 0

    if not args.token:
        sys.exit("Pass --token or set NASHIUM_WORKER_TOKEN")

    if not worker_bin.exists():
        sys.exit(f"nashium-worker not found at {worker_bin}. Re-run without --fast.")

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    from nashium.worker.supervisor import run_supervised_workers

    if args.wt_tabs:
        ui_mode = "wt-tabs"
    elif args.plain:
        ui_mode = "plain"
    elif args.tabs:
        ui_mode = "tabs"
    else:
        ui_mode = "auto"

    worker_cmd = [
        str(worker_bin),
        "--api-url",
        args.api_url,
        "--token",
        args.token,
        "--log-level",
        args.log_level,
    ]
    return run_supervised_workers(
        worker_cmd=worker_cmd,
        count=args.workers,
        cwd=ROOT,
        api_url=args.api_url,
        shutdown_timeout=args.shutdown_timeout,
        ui_mode=ui_mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
