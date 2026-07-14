# Nashium Server Runner

Worker for executing Nashium competition matches in Docker containers.

This is the server-side component that runs matches between submitted bots and leaderboard bots using Docker containerization for isolation and resource management.

---

## Installation

Before installing, make sure you are in the correct directory:

```bash
cd Nashium-SDK
```

Then install the package:

```bash
pip install -e .
```

---

## Usage

The worker connects to a Nashium server API to claim and execute matches.

### Environment Variables

- `NASHIUM_API_URL` - Base URL of the Nashium server (default: `http://localhost:8080`)
- `NASHIUM_WORKER_TOKEN` - Authentication token for the worker API (required)
- `NASHIUM_SEED_SECRET` - Secret key for deterministic seed generation (required)
- `NASHIUM_ROUNDS` - Number of rounds per match (default: `10000`)
- `NASHIUM_MAX_TIME` - Max CPU time per bot in seconds (default: `100.0`)
- `NASHIUM_POLL_INTERVAL` - Seconds between polls when work is available (default: `2.0`)
- `NASHIUM_IDLE_INTERVAL` - Seconds between polls when idle (default: `10.0`)
- `NASHIUM_LOG_LEVEL` - Logging level (default: `INFO`)

### Running the Worker

```bash
nashium-worker --api-url http://localhost:8080 --token YOUR_TOKEN --seed-secret YOUR_SECRET
```

Or with environment variables:

```bash
export NASHIUM_API_URL=http://localhost:8080
export NASHIUM_WORKER_TOKEN=xxx
export NASHIUM_SEED_SECRET=yyy
nashium-worker
```

### WSL2 Note

If running in WSL2 and the Nashium server is on Windows, use the `--wsl-host` flag to auto-detect the Windows host IP:

```bash
nashium-worker --wsl-host --token "xxx" --seed-secret "yyy"
```

### Single Match Mode

For testing, run a single match and exit:

```bash
nashium-worker --single --api-url http://localhost:8080 --token YOUR_TOKEN --seed-secret YOUR_SECRET
```

---

## Docker Setup

The worker uses Docker for sandboxed execution. Make sure Docker is installed and running.

Build the Docker image:

```bash
cd src/nashium/server
docker build -t nashium-runner:latest .
```

---

## Configuration

The worker can be configured with the following command-line options:

- `--api-url` - Base URL of the Nashium server
- `--token` - Worker authentication token (required)
- `--seed-secret` - Secret key for seed generation (required)
- `--rounds` - Number of rounds per match (default: `10000`)
- `--max-time` - Max CPU time per bot in seconds (default: `100.0`)
- `--poll-interval` - Seconds between polls when work is available (default: `2.0`)
- `--idle-interval` - Seconds between polls when idle (default: `10.0`)
- `--log-level` - Logging level (default: `INFO`)
- `--wsl-host` - Auto-detect WSL2 host IP for API URL
- `--single` - Run a single match and exit (for testing)

---

## Architecture

The worker consists of:

- **Worker** - Main loop that polls for work, executes matches, and submits results
- **NashiumClient** - HTTP client for communicating with the server API
- **DockerExecutor** - Executes bot code in isolated Docker containers
- **DockerBackend** - Backend abstraction for Docker-based match execution

---

## Match Execution

1. Worker polls server for next queued interaction
2. Retrieves bot code for both submitted and leaderboard bots
3. Validates bot code syntax
4. Generates deterministic seed from bot codes and secret
5. Executes match in Docker containers with resource limits
6. Collects runtime statistics (CPU, memory usage)
7. Submits results to server API
