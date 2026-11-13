# Nashium SDK & Match Worker

Sandboxed match runner and worker for executing Nashium competition matches in Docker containers against **`https://nashium.com`**.

---

## Quick Start (WSL2 or Linux)

See **[GETTING_STARTED.md](GETTING_STARTED.md)** for full new-machine setup instructions.

```bash
# First-time setup (.venv + nashium-runner:latest image)
python3 scripts/bootstrap.py --setup-only

# Launch 6 workers in the interactive per-worker tabbed console
python3 scripts/bootstrap.py --fast --token YOUR_TOKEN --workers 6
```

Do **not** wrap the worker itself in Docker — each worker runs on the Linux/WSL2 host and spawns isolated Docker containers per bot match.

---

## Manual Installation

From the repository root (inside WSL2 or Linux):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
docker build -t nashium-runner:latest src/nashium/server/
```

---

## Usage

### Running Workers

Single worker:

```bash
nashium-worker --token YOUR_TOKEN
```

Multiple workers (opens the per-worker tabbed console with `1`–`9` tab switching, **120s safe `Ctrl+C` drain**, and **`/kill`** instant force-kill):

```bash
nashium-worker --token YOUR_TOKEN --workers 6
```

Single match mode (claim and execute at most one match, then exit):

```bash
nashium-worker --single --token YOUR_TOKEN
```

### Environment Variables

- `NASHIUM_API_URL` — Base URL of the Nashium server (default: `https://nashium.com`)
- `NASHIUM_WORKER_TOKEN` — Authentication token for the worker API (required)
- `NASHIUM_ROUNDS` — Number of rounds per match (default: `10000`)
- `NASHIUM_MAX_TIME` — Max CPU time per bot in seconds (default: `100.0`)
- `NASHIUM_MAX_WALL` — Wall-clock hang guard per bot in seconds (default: `300.0`)
- `NASHIUM_MAX_MEMORY_MB` — Memory limit per bot container in MB (default: `200`)
- `NASHIUM_IDLE_INTERVAL` — Seconds between polls when idle, ±20% jitter (default: `10.0`)
- `NASHIUM_LOG_LEVEL` — Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`)
- `NASHIUM_WORKER_NAME` — Optional worker tag for logs, e.g. `W1`

### CLI Options (`nashium-worker`)

- `--api-url` — Base URL of the Nashium server (default: `https://nashium.com`)
- `--token` — Worker authentication token
- `--workers` — Number of worker processes to supervise (default: `1`)
- `--shutdown-timeout` — Seconds to wait for in-flight matches on shutdown before force-killing (default: `120`)
- `--tabs` / `--plain` / `--wt-tabs` — Force tabbed TUI, combined stream, or native Windows Terminal WSL2 tabs
- `--name` — Optional worker name shown in logs
- `--rounds` — Number of rounds per match (default: `10000`)
- `--max-time` — Max CPU time per bot in seconds (default: `100.0`)
- `--max-wall` — Wall-clock hang guard per bot in seconds (default: `300.0`)
- `--max-memory-mb` — Max RAM per bot in MB (default: `200`)
- `--idle-interval` — Idle poll interval in seconds (default: `10.0`)
- `--log-level` — Logging level (default: `INFO`)
- `--wsl-host` — Resolve API host to the WSL2 default gateway (only for local dev against a server running on the Windows host)
- `--single` — Claim and run at most one match, then exit

---

## Architecture

- **`Worker` (`nashium.worker.runner`)** — Claims matches, runs them in Docker, caches completed results until submitted, and blocks/retries on harness faults without stranding or skipping matches
- **`WorkerSupervisor` (`nashium.worker.supervisor`)** — Multi-worker launcher with staggered startup, per-worker log tabs, 120s graceful drain, and `/kill` instant force-kill + container reaper
- **`NashiumClient` (`nashium.worker.client`)** — HTTP client for `/api/worker/claim-next` and `/api/worker/result`
- **`DockerExecutor` (`nashium.server.docker`)** — Executes untrusted bot code in network-disabled, read-only, resource-capped Docker containers over Unix domain sockets
