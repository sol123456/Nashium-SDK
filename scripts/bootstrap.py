#!/usr/bin/env python3
"""One-shot setup + optional multi-worker launcher.

Works on Windows, macOS, and Linux. Run from the repo root:

    python scripts/bootstrap.py --token YOUR_TOKEN --workers 4

What this does:
  1. Creates .venv if missing
  2. pip install -e . and docker
  3. docker build of nashium-runner:latest
  4. Starts N worker processes (default 1)

Do NOT put workers inside Docker. Each worker talks to Docker on the host
to sandbox bot matches. Nested Docker is more pain than it is worth.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
DOCKERFILE_DIR = ROOT / "src" / "nashium" / "server"
IMAGE = "nashium-runner:latest"


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


def ensure_python() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Need Python 3.10+. Install from https://www.python.org/downloads/")


def ensure_docker() -> None:
    if not shutil.which("docker"):
        sys.exit(
            "Docker is not on PATH.\n"
            "Install Docker Desktop: https://docs.docker.com/get-docker/\n"
            "Then start it and re-run this script."
        )
    try:
        subprocess.check_call(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        sys.exit("Docker is installed but the daemon is not running. Start Docker Desktop.")


def ensure_venv() -> Path:
    py = venv_python()
    if not py.exists():
        print(f"Creating venv at {VENV}")
        run([sys.executable, "-m", "venv", str(VENV)])
    return py


def install(py: Path) -> None:
    run([str(py), "-m", "pip", "install", "-U", "pip"])
    run([str(py), "-m", "pip", "install", "-e", str(ROOT)])
    run([str(py), "-m", "pip", "install", "docker"])


def build_image() -> None:
    run(
        ["docker", "build", "-t", IMAGE, "-f", str(DOCKERFILE_DIR / "Dockerfile"), str(DOCKERFILE_DIR)]
    )


def extra_worker_flags() -> list[str]:
    flags: list[str] = []
    # WSL2: Windows Docker Desktop + Linux Python often needs --wsl-host
    if Path("/proc/version").exists():
        try:
            ver = Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            ver = ""
        if "microsoft" in ver:
            flags.append("--wsl-host")
    return flags


def start_workers(n: int, token: str, extra: list[str]) -> None:
    worker = venv_nashium()
    if not worker.exists():
        sys.exit(f"nashium-worker not found at {worker}. Install failed?")
    procs: list[subprocess.Popen] = []
    cmd_base = [str(worker), "--token", token, *extra]
    print(f"Starting {n} worker(s): {' '.join(cmd_base)}")
    try:
        for i in range(n):
            print(f"--- worker {i + 1}/{n} ---")
            procs.append(subprocess.Popen(cmd_base, cwd=str(ROOT)))
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        print("\nStopping workers...")
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Set up Nashium worker and optionally run N copies")
    p.add_argument("--token", default=os.environ.get("NASHIUM_WORKER_TOKEN", ""), help="Worker API token")
    p.add_argument("--workers", type=int, default=1, help="How many workers to start (default 1)")
    p.add_argument("--setup-only", action="store_true", help="Install + docker build, do not start workers")
    p.add_argument("--skip-docker-build", action="store_true")
    p.add_argument("--api-url", default=os.environ.get("NASHIUM_API_URL", ""))
    return p.parse_args()


def main() -> int:
    args = parse_args()
    os.chdir(ROOT)
    ensure_python()
    ensure_docker()
    py = ensure_venv()
    install(py)
    if not args.skip_docker_build:
        build_image()

    if args.setup_only:
        print("Setup done. Start later with:")
        print(f"  {venv_nashium()} --token YOUR_TOKEN")
        return 0

    if not args.token:
        sys.exit("Pass --token or set NASHIUM_WORKER_TOKEN")

    extra = extra_worker_flags()
    if args.api_url:
        extra.extend(["--api-url", args.api_url])
    start_workers(args.workers, args.token, extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
