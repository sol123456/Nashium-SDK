"""Configuration resolution for ``nashium farm``.

Precedence, highest wins:

1. Command line flags
2. Environment variables (``NASHIUM_*``)
3. ``~/.nashium/farm.ini`` (``[farm]`` section)
4. Built-in defaults

Anything that is secret (token, seed secret) is handed to the child workers
through the environment rather than the command line, so it does not show up in
``ps``/Task Manager.
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

DEFAULT_API_URL = "http://localhost:8080"
DEFAULT_ROUNDS = 10_000
DEFAULT_MAX_TIME = 100.0
DEFAULT_SHUTDOWN_TIMEOUT = 240.0


class ConfigError(Exception):
    """Raised when the farm configuration is unusable."""


def default_config_path() -> Path:
    return Path.home() / ".nashium" / "farm.ini"


def load_config_file(path: Path) -> dict[str, str]:
    """Read the ``[farm]`` section of an INI file. Missing file -> empty dict."""
    if not path.exists():
        return {}
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    if not parser.has_section("farm"):
        return {}
    return dict(parser.items("farm"))


def _as_int(value: object, name: str) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be an integer (got {value!r})") from None


def _as_float(value: object, name: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a number (got {value!r})") from None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_secret(text: Optional[str], file_path: Optional[str], name: str) -> Optional[str]:
    """Pick a secret from ``--flag`` or from ``--flag-file``."""
    if text is not None and file_path is not None:
        raise ConfigError(f"Use either {name} or {name}-file, not both")
    if file_path is not None:
        try:
            return Path(file_path).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"Could not read {name}-file {file_path}: {exc}") from exc
    return text


@dataclass
class FarmConfig:
    """Everything the supervisor needs, already resolved."""

    api_url: str = DEFAULT_API_URL
    token: str = ""
    seed_secret: str = ""

    workers: int = 1

    # Forwarded verbatim to each worker.
    rounds: Optional[int] = None
    max_time: Optional[float] = None
    poll_interval: Optional[float] = None
    idle_interval: Optional[float] = None
    log_level: str = "INFO"
    wsl_host: bool = False

    # Supervisor behaviour.
    restart: bool = True
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT
    log_dir: Optional[str] = "logs"
    quiet: bool = False
    color: Optional[bool] = None
    ignore_cpu_check: bool = False
    skip_health_check: bool = False
    clean_orphans: bool = False
    build_image: bool = False
    check_only: bool = False
    save_config: bool = False
    config_path: Path = field(default_factory=default_config_path)

    # ---------------------------------------------------------------- children

    def child_argv(self) -> list[str]:
        """Command line for one worker. Contains no secrets."""
        argv = [sys.executable, "-m", "nashium.worker"]
        if self.rounds is not None:
            argv += ["--rounds", str(self.rounds)]
        if self.max_time is not None:
            argv += ["--max-time", str(self.max_time)]
        if self.poll_interval is not None:
            argv += ["--poll-interval", str(self.poll_interval)]
        if self.idle_interval is not None:
            argv += ["--idle-interval", str(self.idle_interval)]
        if self.wsl_host:
            argv += ["--wsl-host"]
        if self.log_level:
            argv += ["--log-level", self.log_level]
        return argv

    def child_env(self, base: Optional[Mapping[str, str]] = None) -> dict[str, str]:
        """Environment for one worker: secrets go here, never on argv."""
        env = dict(os.environ if base is None else base)
        env["NASHIUM_API_URL"] = self.api_url
        env["NASHIUM_WORKER_TOKEN"] = self.token
        env["NASHIUM_SEED_SECRET"] = self.seed_secret
        # Without these, child output is block-buffered when piped and the
        # console shows nothing until a match finishes.
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env


def resolve(args, env: Optional[Mapping[str, str]] = None) -> FarmConfig:
    """Merge flags / environment / config file into a :class:`FarmConfig`."""
    env = os.environ if env is None else env

    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    file_values = load_config_file(config_path)

    def pick(flag_value, env_name, file_name, default=None):
        if flag_value is not None and flag_value != "":
            return flag_value
        if env_name and env.get(env_name):
            return env[env_name]
        if file_name and file_values.get(file_name):
            return file_values[file_name]
        return default

    token = _read_secret(
        pick(args.token, "NASHIUM_WORKER_TOKEN", "token"),
        getattr(args, "token_file", None),
        "token",
    )
    seed_secret = _read_secret(
        pick(args.seed_secret, "NASHIUM_SEED_SECRET", "seed_secret"),
        getattr(args, "seed_secret_file", None),
        "seed_secret",
    )

    workers = pick(args.workers, "NASHIUM_WORKERS", "workers")
    if workers is None:
        workers = os.cpu_count() or 2
    workers = _as_int(workers, "workers") or 1
    if workers < 1:
        raise ConfigError("--workers must be at least 1")

    log_dir = pick(args.log_dir, "NASHIUM_LOG_DIR", "log_dir", "logs")
    if getattr(args, "no_logs", False):
        log_dir = None

    return FarmConfig(
        api_url=str(pick(args.api_url, "NASHIUM_API_URL", "api_url", DEFAULT_API_URL)).rstrip("/"),
        token=token or "",
        seed_secret=seed_secret or "",
        workers=workers,
        rounds=_as_int(pick(args.rounds, "NASHIUM_ROUNDS", "rounds", None), "rounds"),
        max_time=_as_float(pick(args.max_time, "NASHIUM_MAX_TIME", "max_time", None), "max_time"),
        poll_interval=_as_float(
            pick(args.poll_interval, "NASHIUM_POLL_INTERVAL", "poll_interval", None), "poll_interval"
        ),
        idle_interval=_as_float(
            pick(args.idle_interval, "NASHIUM_IDLE_INTERVAL", "idle_interval", None), "idle_interval"
        ),
        log_level=str(pick(args.log_level, "NASHIUM_LOG_LEVEL", "log_level", "INFO")).upper(),
        wsl_host=bool(args.wsl_host or _as_bool(file_values.get("wsl_host"))),
        restart=not args.no_restart,
        shutdown_timeout=_as_float(args.shutdown_timeout, "shutdown_timeout") or DEFAULT_SHUTDOWN_TIMEOUT,
        log_dir=str(Path(log_dir).expanduser()) if log_dir else None,
        quiet=bool(args.quiet),
        color=None if args.color is None else _as_bool(args.color),
        ignore_cpu_check=bool(args.ignore_cpu_check),
        skip_health_check=bool(args.skip_health_check),
        clean_orphans=bool(args.clean_orphans),
        build_image=bool(args.build_image),
        check_only=bool(args.check),
        save_config=bool(args.save_config),
        config_path=config_path,
    )


def write_config(config: FarmConfig, path: Optional[Path] = None) -> Path:
    """Write the resolved settings to an INI file (chmod 600 on POSIX)."""
    path = path or config.config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "; Nashium farm settings. Secrets live here so you do not retype them.",
        "; Keep this file private.",
        "[farm]",
        f"api_url = {config.api_url}",
        f"token = {config.token}",
        f"seed_secret = {config.seed_secret}",
        f"workers = {config.workers}",
    ]
    if config.rounds is not None:
        lines.append(f"rounds = {config.rounds}")
    if config.max_time is not None:
        lines.append(f"max_time = {config.max_time}")
    if config.log_level:
        lines.append(f"log_level = {config.log_level}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name == "posix":
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path
