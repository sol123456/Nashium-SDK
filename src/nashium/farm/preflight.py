"""Pre-flight checks for ``nashium farm``.

Everything here is about turning the four-command ritual

    python3 -m venv venv && source venv/bin/activate
    pip install -e .
    pip install docker
    docker build -t nashium-runner:latest -f src/nashium/server/Dockerfile src/nashium/server/

into "it tells you what is missing and can fix the bits it is allowed to fix".

All checks shell out to the ``docker`` CLI rather than the docker Python SDK so
that pre-flight still works when the SDK is not installed yet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

IMAGE = "nashium-runner:latest"

# Each bot container is capped at 0.5 CPU (cpu_quota 50000 / period 100000) and a
# match runs two of them, so a worker saturates roughly one core.
CONTAINERS_PER_WORKER = 2
BYTES_PER_BOT_DEFAULT = 200 * 1024 * 1024


@dataclass
class CheckResult:
    ok: bool
    message: str
    fix: Optional[str] = None
    fatal: bool = True


def _docker() -> Optional[str]:
    return shutil.which("docker")


def check_docker() -> CheckResult:
    docker = _docker()
    if not docker:
        return CheckResult(
            False,
            "docker CLI not found on PATH",
            "Install Docker Desktop (Windows/macOS) or docker-ce (Linux), then reopen your terminal.",
        )
    try:
        proc = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(False, "docker daemon did not answer within 20s", "Is Docker Desktop still starting?")
    except OSError as exc:
        return CheckResult(False, f"could not run docker: {exc}")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = "Start Docker Desktop / 'sudo systemctl start docker'."
        if detail and "permission denied" in detail[-1].lower():
            hint = "Add yourself to the docker group: 'sudo usermod -aG docker $USER' then log out and in."
        elif detail and "cannot connect" in detail[-1].lower():
            hint = "Docker daemon is not running. Start Docker Desktop or the docker service."
        return CheckResult(False, f"docker daemon unreachable ({detail[-1] if detail else 'no output'})", hint)

    version = proc.stdout.strip() or "unknown"
    return CheckResult(True, f"docker daemon reachable (server {version})")


def check_image() -> CheckResult:
    docker = _docker()
    if not docker:
        return CheckResult(False, f"cannot check image {IMAGE}: docker CLI missing")
    proc = subprocess.run(
        [docker, "image", "inspect", IMAGE, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        return CheckResult(True, f"runner image present ({IMAGE}, {proc.stdout.strip()[:19]})")
    return CheckResult(
        False,
        f"runner image {IMAGE} not built",
        "Run 'nashium farm --build-image' once (or 'nashium farm --build-image --check').",
    )


def image_context_dir() -> Path:
    """The build context for the runner image, resolved from the package.

    This is why the farm can build the image from any working directory, unlike
    'docker build -f src/nashium/server/Dockerfile src/nashium/server/'.
    """
    import nashium

    return Path(nashium.__file__).resolve().parent / "server"


def build_image(console) -> CheckResult:
    docker = _docker()
    if not docker:
        return CheckResult(False, "docker CLI not found; cannot build the image")

    context = image_context_dir()
    if not (context / "Dockerfile").exists():
        return CheckResult(False, f"Dockerfile not found at {context / 'Dockerfile'}")

    console.system(f"Building {IMAGE} from {context} ...")
    proc = subprocess.run([docker, "build", "-t", IMAGE, str(context)])
    if proc.returncode != 0:
        return CheckResult(False, f"docker build failed (exit {proc.returncode})")
    return CheckResult(True, f"built {IMAGE}")


def check_server(api_url: str, token: str, timeout: float = 8.0) -> CheckResult:
    """Mirror of ``NashiumClient.health_check`` without needing requests."""
    url = api_url.rstrip("/") + "/api/worker/health"
    request = urllib.request.Request(url, headers={"X-Worker-Token": token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 200:
                return CheckResult(True, f"server healthy at {url}")
            return CheckResult(False, f"server answered HTTP {response.status} at {url}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return CheckResult(
                False,
                f"server rejected the worker token (HTTP {exc.code})",
                "Check --token / NASHIUM_WORKER_TOKEN against the server's configured worker token.",
            )
        return CheckResult(False, f"server answered HTTP {exc.code} at {url}")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        hint = None
        if "Nomad" in api_url or "localhost" in api_url or "127.0.0.1" in api_url:
            hint = (
                "If the server runs on Windows and the worker runs in WSL2, the server is not on "
                "localhost from inside WSL. Use --wsl-host, or the host IP."
            )
        return CheckResult(False, f"cannot reach server at {url}: {reason}", hint)
    except Exception as exc:  # socket.timeout and friends
        return CheckResult(False, f"cannot reach server at {url}: {exc}")


def cpu_and_memory_advice(workers: int) -> list[CheckResult]:
    """Warn (never fail) about oversubscribing the machine.

    Why this matters: the 100s per-bot budget in ``server/docker.py`` is
    wall-clock (``time.perf_counter()`` around each IPC round-trip), not CPU time.
    If you run more workers than you have cores, a bot that is well within budget
    still gets flagged ``timed_out``, scores 0 for its remaining moves, and the
    server stores a wrong result for it.
    """
    results: list[CheckResult] = []
    cores = os.cpu_count() or 1

    if workers > cores:
        results.append(
            CheckResult(
                False,
                f"{workers} workers on {cores} logical cores - oversubscribed",
                "Each worker runs one match = two containers at 0.5 CPU. Beyond one worker per core the "
                "wall-clock 100s budget is no longer representative and good bots can be marked as timed out. "
                "Use --workers "
                f"{cores} (or fewer), or pass --ignore-cpu-check if you know what you are doing.",
                fatal=False,
            )
        )
    elif workers > max(1, cores // 2):
        results.append(
            CheckResult(
                False,
                f"{workers} workers on {cores} logical cores - close to the limit",
                "Fine if matches are short; watch for 'timed out' in worker logs if you also use the machine.",
                fatal=False,
            )
        )

    worst_case_gb = workers * CONTAINERS_PER_WORKER * BYTES_PER_BOT_DEFAULT / (1024 ** 3)
    try:
        total_gb = _total_memory_gb()
    except Exception:
        total_gb = None
    if total_gb is not None and worst_case_gb > total_gb * 0.6:
        results.append(
            CheckResult(
                False,
                f"{workers} workers can hold up to {worst_case_gb:.1f} GB of bot containers "
                f"(machine has {total_gb:.0f} GB)",
                "Bots are capped at 200 MB each, so this is a worst case, not a forecast.",
                fatal=False,
            )
        )
    return results


def _total_memory_gb() -> Optional[float]:
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / (1024 ** 3)
        except Exception:
            return None

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024 ** 2)
    except OSError:
        return None
    return None


def find_orphan_containers() -> list[str]:
    """Containers made from the runner image that are still lying around."""
    docker = _docker()
    if not docker:
        return []
    try:
        proc = subprocess.run(
            [docker, "ps", "-a", "--filter", f"ancestor={IMAGE}", "--format", "{{.ID}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def remove_orphan_containers(console) -> int:
    orphans = find_orphan_containers()
    if not orphans:
        return 0
    docker = _docker()
    ids = [line.split("\t")[0] for line in orphans]
    console.warn(f"removing {len(ids)} leftover runner container(s)")
    try:
        subprocess.run([docker, "rm", "-f", *ids], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        console.warn(f"could not remove leftover containers: {exc}")
        return 0
    return len(ids)
