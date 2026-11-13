# Running many workers

You run six terminals, each with:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e . && pip install docker
docker build -t nashium-runner:latest -f src/nashium/server/Dockerfile src/nashium/server/
nashium-worker --wsl-host --token "REDACTED"
```

This page is about doing that with one command, and about which parts of that
ritual are actually load-bearing.

---

## Short version

```bash
# once per machine
./scripts/setup.sh                       # Windows: .\scripts\setup.ps1
nashium farm --save-config --token "..." --seed-secret "..."

# every time
nashium farm --workers 6
```

You get one terminal, six colour-prefixed output streams, a log file per worker
under `logs/farm-<timestamp>/`, automatic restart of workers that die, and a
Ctrl-C that lets in-flight matches finish instead of killing them.

---

## Should it be a Docker container that runs more Docker containers?

**No.** Not for the worker. Two reasons.

### 1. The worker cannot talk to the host daemon about paths it owns

The worker does not pass bot code to containers as files on a volume. It creates
a Unix socket inside a temp directory **on its own filesystem** and bind-mounts
that directory into the container (`src/nashium/server/docker.py`):

```python
self._sock_dir = tempfile.mkdtemp(prefix="nashium_")        # e.g. /tmp/nashium_x1y2
...
volumes={self._sock_dir: {"bind": "/ipc", "mode": "rw"}},
```

If the worker itself runs in a container and talks to the host daemon (Docker
"outside of" Docker, i.e. mounting `/var/run/docker.sock`), then `/tmp/nashium_x1y2`
exists inside the worker container and **not on the host**. Docker resolves bind
mount paths on the daemon's side, so the match container would get an empty
directory and the worker would hang waiting for a bot that can never connect.

Making that work means mounting a shared volume at an identical path in both the
worker container and the match container, and making the worker use that path
instead of `tempfile.mkdtemp`. That is a code change to the sandbox layer, for
zero benefit - it does not make anything safer or faster.

### 2. Docker-in-Docker would be worse, not better

Real DinD (a nested daemon) needs `--privileged`. You would be handing a
privileged container the job of running untrusted competition code. The whole
point of `nashium-runner` is that the *untrusted* part is isolated: read-only
root, no network, no capabilities, `no-new-privileges`, 200 MB and 0.5 CPU.
Containerising the *trusted* part does not improve that, and `--privileged`
would quietly undo it.

### What is already right

Notice what the worker actually does: it is a thin HTTP client. Poll the server,
claim a match, spawn two sandboxed containers, submit the result. The risky part
is already containerised, per match, by the code you already have. The worker
itself has no reason to be.

### So what is the "runs on any machine" answer?

The worker needs exactly two things: a Python (3.10+) and a Docker daemon. Both
are one-line installs everywhere. What was painful was not packaging, it was the
*setup ritual*: a virtualenv per checkout, two pip installs, and a `docker build`
that only works from the repo root.

`nashium farm` collapses that:

| before | now |
| --- | --- |
| `python3 -m venv venv` + activate | `./scripts/setup.sh` (once per machine) |
| `pip install -e .` + `pip install docker` | same script (`pip install -e ".[worker]"`) |
| `docker build -f src/nashium/server/Dockerfile src/nashium/server/` | `nashium farm --build-image` (finds the context itself, works from any directory) |
| six terminals, six `nashium-worker ...` | `nashium farm --workers 6` |
| `nashium-worker` not on PATH on Windows | `nashium farm` launches `python -m nashium.worker`, same interpreter, no PATH needed |

---

## How many workers?

**One per physical core. Never more than one per logical core.**

The reason is a detail that is worth knowing, because it silently corrupts
results rather than failing loudly:

- each bot container is capped at **0.5 CPU** (`cpu_quota=50000`, `cpu_period=100000`),
  and a match runs **two** of them, so one worker ≈ one saturated core;
- the **100 second per-bot budget is wall-clock**, not CPU time
  (`time.perf_counter()` around each request/response over the IPC socket in
  `server/docker.py`).

So if you run 12 workers on 8 cores, each match gets ~0.66 of a core, every bot
runs ~1.5x slower than it measures on its own, and a bot that is comfortably
inside 100 s of *compute* can cross 100 s of *elapsed* time. When that happens
`timed_out` is set, the bot returns `0` for the rest of the match, and **the
server stores that as the real result**. Nothing warns you; the leaderboard just
gets slower-looking bots than you have.

`nashium farm` prints a warning if your `--workers` exceeds the core count. You
can silence it with `--ignore-cpu-check`, but you should not need to.

A sane layout:

| machine | workers |
| --- | --- |
| 4-core laptop, also doing other things | 2-3 |
| 8-core desktop, dedicated | 6-8 |
| 16-core server | 12-16 |
| CI runner / small VPS | 1-2 |

Memory: 200 MB cap per bot, two bots per match, so budget ~400 MB per worker in
the worst case (normal bots use far less).

---

## Why processes and not threads?

Six workers in one process would be six threads whose only real work is blocking
`requests` calls and waiting on Docker. That works, but it means one bad match
takes down all six, one leaked container stalls the rest, and a stuck worker
cannot be killed on its own. Six child processes are the same thing you run by
hand today, so the results and the logs are identical to what you already trust,
and `docker stats` shows one tree per worker.

---

## Behaviour worth knowing

**Ctrl-C is graceful.** The supervisor sends a terminate signal to each worker;
each worker finishes the match it is in the middle of, submits the result, and
exits. The default wait is 240 s, enough for a worst-case match (two bots at the
100 s budget plus container startup). A second Ctrl-C kills immediately.

Your current workflow does not do this: Ctrl-C kills the worker mid-match, the
result is never submitted, and the two containers are left behind.

**Unexpected exits are restarted** with exponential backoff (5 s, 10 s, 20 s, up
to 60 s), and the counter resets after a worker has been healthy for 5 minutes.
`--no-restart` turns it off.

**A rejected token stops the farm.** If the server answers 401, restarting is
pointless and six workers would hammer it. The farm stops with exit code 4 and
tells you to fix the token.

**Leftover containers are reported.** If a worker is killed hard, its match
containers can survive. The farm counts them at exit and points at
`nashium farm --clean-orphans`.

**Secrets are not on the command line.** The token and seed secret are passed to
child processes through the environment, so they do not appear in `ps`, Task
Manager, or a `docker inspect`. `--save-config` writes them to
`~/.nashium/farm.ini` with `chmod 600`.

---

## Files

```
logs/farm-20261113-142501/worker-01.log   full output per worker
~/.nashium/farm.ini                       saved settings (token, seed secret, workers)
```

`logs/` is in `.gitignore`.

---

## When a container *does* make sense

If you want a worker host that is disposable, containerise the **whole host**,
not the worker: a small VM or a container with a Docker socket and the repo
mounted, treating it as "a machine that has Python and Docker". The farm runs
inside it unchanged.

And if you ever do want the worker in a container, the pre-requisite is changing
the IPC socket directory to a path that is a shared volume mounted identically
inside the worker container and the match container. Until then, Docker-outside-
of-Docker is a trap here.
