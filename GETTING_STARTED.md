# Getting Nashium workers running on a PC

Workers run on the host (inside **WSL2** on Windows, or directly on Linux) and use Docker only to sandbox individual bot matches — never Docker-in-Docker.

To start 6 workers with a **dedicated tab per worker** from a single command:

```bash
python3 scripts/bootstrap.py --token YOUR_TOKEN --workers 6
```

After the first setup, add `--fast` (or run `.venv/bin/nashium-worker --token YOUR_TOKEN --workers 6`) to skip `pip install` and `docker build` and launch immediately.

---

## Multi-worker tabbed console & safe shutdown

When `--workers` is 2 or more, `bootstrap.py` opens an interactive tabbed console right inside your Windows Terminal / WSL2 tab:

- **`1`–`9`**, **`Tab` / `Shift+Tab`**, or **`←` / `→`** — switch between individual worker tabs (`W1`, `W2`, …)
- **`0`** (or **`a`**) — view combined logs from all workers
- **`↑` / `↓`**, **`PgUp` / `PgDn`**, **`Home` / `End`** (or **`g`**) — scroll through a worker's history and return to live tail
- **`Ctrl+C`** (or **`/stop` + Enter**) — **safe shutdown**: stops claiming new matches and gives workers up to **120 seconds** (`--shutdown-timeout 120`) to finish and submit any in-flight match so nothing is stranded in `EXECUTING` on `nashium.com`
- **`/kill` + Enter** — **instant kill switch** (available anytime via `/` or during shutdown): immediately `SIGKILL`s all remaining worker processes and reaps their `nashium-runner` Docker containers

### The overview stays open until *you* close it

When the last worker stops — a crash, a revoked token, a finished drain, or `/kill` — the console no longer disappears with it. It freezes on a final overview and waits for you:

- The top banner reports the outcome, e.g. `SESSION ENDED WITH ERRORS: 1/6 worker(s) failed (W3 rc=1)` (red) or `SESSION ENDED: all 6 worker(s) stopped cleanly` (green), plus how many matches were submitted and the total run time.
- A worker that gave up on a fatal problem — a revoked token, a Python traceback — counts as an error even when it exited `0`: its tab badge reads `!err`, the banner names the cause, and the process exits `1` instead of pretending everything was fine.
- Every worker keeps its tab with its final state (`rc=1`, `killed`, `done`), so nothing is lost. If a worker died badly, its tab is focused for you automatically.
- All navigation still works while it is held: `1`–`9` / `Tab` / `0`, `↑`/`↓`, `PgUp`/`PgDn`, `Home`/`End` to scroll back through up to 5 000 lines of that worker's history.
- Close it whenever you are done with **`q`**, **`Esc`** or **`Ctrl+C`**. Only then does the process exit, with the usual exit code (`0` clean, `1` a worker failed, `130` force-killed).
- As it closes, the last lines of every failed worker are echoed to `stderr`, so the post-mortem survives in your terminal scrollback too.

For unattended or scripted runs, pass **`--auto-exit`** to get the old behaviour (the console closes by itself as soon as every worker has stopped). `--plain` mode never clears the screen, so its logs stay in the scrollback either way.

*(Optional UI modes: `--plain` streams combined `[W1]`…`[WN]` logs without the TUI; `--wt-tabs` opens separate native Windows Terminal tabs via `wt.exe`.)*

---

## What you need on a Windows PC

1. **Windows Terminal + WSL2 (Ubuntu)**
   ```powershell
   wsl --install
   ```
   Run all git / Python / worker commands **inside your WSL2 Linux terminal** (not Windows PowerShell — Linux bot containers communicate with the worker over Linux `AF_UNIX` sockets).
2. **Docker Desktop** (must be running, with **Settings → Resources → WSL Integration** enabled for your WSL2 distro)
   - https://docs.docker.com/get-docker/
3. **Python 3.10+ & Git inside WSL2**
   ```bash
   sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
   ```

*(Sizing tip: each active worker uses ~1 CPU core and up to ~400 MB of container RAM. Aim for `workers <= CPU cores - 1` — e.g. 6 workers on an 8-core CPU with ≥6–8 GB RAM available to WSL2.)*

---

## First time on a new PC (inside WSL2 or Linux)

```bash
git clone https://github.com/sol123456/Nashium-SDK.git
cd Nashium-SDK
python3 scripts/bootstrap.py --setup-only
```

This creates `.venv`, installs `nashium-sdk` (including `docker`, `requests`, `pydantic`), and builds `nashium-runner:latest`.

---

## Run workers

```bash
python3 scripts/bootstrap.py --fast --token YOUR_TOKEN --workers 6
```

Workers connect to **`https://nashium.com`** by default (override with `--api-url` or `NASHIUM_API_URL`).

Or activate `.venv` directly:

```bash
source .venv/bin/activate
nashium-worker --token "YOUR_TOKEN" --workers 6
```

---

## Updating later

```bash
cd Nashium-SDK
git pull
python3 scripts/bootstrap.py --setup-only
```

Then start workers as above.
