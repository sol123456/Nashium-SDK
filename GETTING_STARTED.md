# Getting Nashium workers onto another PC

You do **not** want Docker-in-Docker. One worker process on the host, Docker only for sandboxed bot matches.

**Recommendation:** one Python process per worker (what you already do). To run 6, either open 6 terminals or:

```bash
python scripts/bootstrap.py --token YOUR_TOKEN --workers 6
```

Logs from all workers mix in one window. If you want separate consoles, open 6 terminals and run `nashium-worker` in each.

---

## What you need on every machine

1. **Python 3.10+**  
   - Windows: https://www.python.org/downloads/ — tick “Add python.exe to PATH”  
   - macOS: `brew install python` or the python.org installer
2. **Docker Desktop** (must be running)  
   - https://docs.docker.com/get-docker/  
   - Windows: WSL2 backend is fine.  
   - macOS: grant Docker the usual permissions.
3. **Git** — https://git-scm.com/

---

## First time on a new PC (Windows, Mac, or Linux)

In a terminal (PowerShell, cmd, Terminal.app, or WSL):

```bash
git clone https://github.com/sol123456/Nashium-SDK.git
cd Nashium-SDK
git checkout arena/16fc3c32-nashium-sdk
```

Use whatever branch you actually want. Then:

```bash
python scripts/bootstrap.py --setup-only
```

On Windows that is often `py scripts/bootstrap.py --setup-only`.

This creates `.venv`, installs the SDK + the Docker Python library, and builds `nashium-runner:latest`.

---

## Run workers

```bash
python scripts/bootstrap.py --token YOUR_TOKEN --workers 6
```

WSL on Windows: the script adds `--wsl-host` automatically.

Or after setup, activate the venv yourself:

**macOS / Linux / WSL**

```bash
source .venv/bin/activate
nashium-worker --token "YOUR_TOKEN"
```

**Windows PowerShell**

```powershell
.\.venv\Scripts\Activate.ps1
nashium-worker --token "YOUR_TOKEN"
```

If PowerShell blocks scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

---

## Updating another PC later

```bash
cd Nashium-SDK
git pull
python scripts/bootstrap.py --setup-only
```

Then start workers as above.

---

## Why not nest Docker?

The worker is a thin client: poll API → start bot containers → report results. Putting the worker in a container means mounting the Docker socket (`docker.sock`), extra permission issues on Mac/Windows, and no real benefit for “run 6 of these”. Multiple host processes sharing one Docker daemon is the simple path.
