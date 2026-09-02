"""Worker loop: claim -> execute in Docker -> submit.

Two fault classes, two behaviours:

  Bot fault      (OOM / CPU / crash / bad move / unparseable code)
                 -> every remaining move defaults to 0, the match completes,
                    wins count normally, flags are set on the submission.

  Harness fault  (Docker down, stale image, container won't start)
                 -> submit NOTHING. The interaction stays EXECUTING. The worker
                    refuses to claim any other work and retries this exact match
                    every retry_interval_seconds, forever.
"""
from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .api import run_match_result_from_code_strings
from .client import AuthenticationError, NashiumClient, NashiumClientError
from .models import (
    MatchResultSubmissionDTO,
    NextQueuedInteractionDTO,
    RuntimeStatsSubmissionDTO,
)
from ..core.engine import MatchConfig
from ..core.errors import MatchExecutionError
from ..core.match_result import BotStats, MatchResult, RuntimeStats
from ..server.docker import (
    cleanup_stale_containers,
    cleanup_stale_socket_dirs,
    reset_client,
    verify_environment,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkerConfig:
    api_base_url: str = "http://localhost:8080"
    worker_token: str = ""

    rounds: int = 10_000
    stat_sig_win_threshold: int = 5155
    max_time_per_bot: float = 100.0
    max_wall_per_bot: float = 300.0
    max_memory_per_bot: int = 200 * 1024 * 1024

    idle_poll_interval_seconds: float = 10.0

    # Blocked-match retry cadence. The worker never abandons a claimed match.
    retry_interval_seconds: float = 30.0
    # Escalate to ERROR-level logging after this many failed attempts.
    escalate_after_attempts: int = 4

    log_level: str = "INFO"


@dataclass
class _Pending:
    """A claimed interaction the worker is obliged to see through."""
    claimed: NextQueuedInteractionDTO
    attempts: int = 0
    # Set once the match has actually run. Lets us retry a failed *submission*
    # without re-running an expensive match.
    submission: Optional[MatchResultSubmissionDTO] = None
    result: Optional[MatchResult] = None
    first_failure_at: float = field(default_factory=time.monotonic)

    @property
    def interaction_id(self) -> int:
        return self.claimed.interaction.id


# --------------------------------------------------------------------- format

def _mb(n: Optional[int]) -> str:
    return f"{n / (1024 * 1024):.1f}MB" if n else "n/a"


def _pct(value: float, budget: float) -> str:
    return f"{100.0 * value / budget:5.1f}%" if budget else "  n/a"


def _health(rs: RuntimeStats) -> str:
    reason = rs.default_reason
    if reason is None:
        return "ok"
    label = {"ram": "OOM (RAM limit)", "cpu": "TIMEOUT (CPU/wall limit)",
             "error": "CRASHED"}[reason]
    frm = rs.default_from_round if rs.default_from_round is not None else 0
    return f"{label} -> defaulted to 0 from round {frm}"


def _condense(text: str, keep: int = 6) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[-keep:])[:600]


def _build_runtime_stats_dto(
    runtime: RuntimeStats,
    stats: Optional[BotStats],
    moves: tuple[int, ...],
    performance: tuple[int, ...],
) -> RuntimeStatsSubmissionDTO:
    return RuntimeStatsSubmissionDTO(
        timedOut=runtime.timed_out,
        memoryExceeded=runtime.memory_exceeded,
        errored=runtime.errored,
        errorMessage=runtime.error_message[:2000] if runtime.error_message else None,
        maxMemory=float(runtime.memory_bytes_peak) if runtime.memory_bytes_peak else None,
        endCpuTime=runtime.elapsed_time_seconds,
        cpuUsageSamples=list(runtime.cpu_usage_samples) or None,
        ramUsageSamples=list(runtime.ram_usage_samples) or None,
        moves=list(moves) or None,
        performance=list(performance) or None,
        wins=stats.wins if stats else None,
        losses=stats.losses if stats else None,
        entropy=stats.entropy if stats else None,
        sharpeRatio=stats.sharpe_ratio if stats else None,
        returnAutocorrelation=stats.return_autocorrelation if stats else None,
    )


def _to_submission(interaction_id: int, result: MatchResult) -> MatchResultSubmissionDTO:
    leaderboard_performance = tuple(1 - x for x in result.submitted_performance)
    return MatchResultSubmissionDTO(
        interactionId=interaction_id,
        seed=result.seed,
        submitted_wins=result.submitted_wins,
        submitted=_build_runtime_stats_dto(
            result.submitted, result.submitted_stats,
            result.submitted_moves, result.submitted_performance),
        leaderboard=_build_runtime_stats_dto(
            result.leaderboard, result.leaderboard_stats,
            result.leaderboard_moves_effective, leaderboard_performance),
    )


# ---------------------------------------------------------------------- worker

class Worker:
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.client = NashiumClient(base_url=config.api_base_url,
                                    worker_token=config.worker_token)
        self.match_config = MatchConfig(
            rounds=config.rounds,
            stat_sig_win_threshold=config.stat_sig_win_threshold,
            max_total_time_seconds_per_bot=config.max_time_per_bot,
            max_total_memory_bytes_per_bot=config.max_memory_per_bot,
            max_total_wall_seconds_per_bot=config.max_wall_per_bot,
        )
        self._pending: Optional[_Pending] = None
        self._stop = threading.Event()

        logging.basicConfig(
            level=getattr(logging, config.log_level.upper()),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # --------------------------------------------------------------- plumbing

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep, so SIGTERM does not wait out a 30s backoff."""
        self._stop.wait(seconds)

    def _setup_signal_handlers(self) -> None:
        def handler(signum, _frame):
            logger.info("Signal %s received - finishing current work then stopping",
                        signum)
            self._stop.set()
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def _blocked(self, pending: _Pending, reason: str) -> None:
        """Record a harness failure. Submit nothing; hold the match; retry later."""
        pending.attempts += 1
        stuck_for = time.monotonic() - pending.first_failure_at
        level = (logging.ERROR if pending.attempts >= self.config.escalate_after_attempts
                 else logging.WARNING)
        logger.log(level,
                   "BLOCKED on interaction %s (attempt %d, stuck %.0fs): %s",
                   pending.interaction_id, pending.attempts, stuck_for, reason)
        logger.log(level,
                   "  interaction %s stays EXECUTING and no other match will be "
                   "claimed. Retrying in %.0fs.",
                   pending.interaction_id, self.config.retry_interval_seconds)

        # If the daemon restarted, the pooled client connections are dead.
        reset_client()
        try:
            cleanup_stale_containers()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- logging

    def _log_report(self, pending: _Pending, result: MatchResult,
                    elapsed: float) -> None:
        cfg = self.match_config
        s, l = result.submitted, result.leaderboard
        sub = pending.claimed.submittedBot.name or f"bot#{pending.claimed.submittedBot.id}"
        lead = (pending.claimed.leaderboardBot.name
                or f"bot#{pending.claimed.leaderboardBot.id}")
        losses = result.rounds - result.submitted_wins
        overhead = max(0.0, elapsed - s.wall_seconds - l.wall_seconds)

        logger.info("Match %s finished in %.1fs | %s %dW-%dL %s (%.2f%%)",
                    pending.interaction_id, elapsed, sub,
                    result.submitted_wins, losses, lead,
                    result.submitted_win_rate * 100.0)
        logger.info("  cpu    | %s %6.2fs (%s of %.0fs) | %s %6.2fs (%s of %.0fs)"
                    " | harness %.1fs",
                    sub, s.elapsed_time_seconds,
                    _pct(s.elapsed_time_seconds, cfg.max_total_time_seconds_per_bot),
                    cfg.max_total_time_seconds_per_bot,
                    lead, l.elapsed_time_seconds,
                    _pct(l.elapsed_time_seconds, cfg.max_total_time_seconds_per_bot),
                    cfg.max_total_time_seconds_per_bot, overhead)
        logger.info("  ram    | %s %8s peak (%s of %s) | %s %8s peak (%s of %s)",
                    sub, _mb(s.memory_bytes_peak),
                    _pct(s.memory_bytes_peak or 0, cfg.max_total_memory_bytes_per_bot),
                    _mb(cfg.max_total_memory_bytes_per_bot),
                    lead, _mb(l.memory_bytes_peak),
                    _pct(l.memory_bytes_peak or 0, cfg.max_total_memory_bytes_per_bot),
                    _mb(cfg.max_total_memory_bytes_per_bot))

        status = f"  status | {sub}: {_health(s)} | {lead}: {_health(l)}"
        logger.info(status) if (s.healthy and l.healthy) else logger.warning(status)

        for label, rs in ((sub, s), (lead, l)):
            if rs.defaulted:
                logger.warning(
                    "  fault  | %s played %d real round(s), then defaulted. "
                    "Result still counts. Cause: %s",
                    label, rs.rounds_played, _condense(rs.error_message or "unknown"))

        if result.submitted_stats and result.leaderboard_stats:
            logger.debug("  quant  | %s H=%.4f SR=%+.2f AC=%+.3f "
                         "| %s H=%.4f SR=%+.2f AC=%+.3f",
                         sub, result.submitted_stats.entropy or 0.0,
                         result.submitted_stats.sharpe_ratio or 0.0,
                         result.submitted_stats.return_autocorrelation or 0.0,
                         lead, result.leaderboard_stats.entropy or 0.0,
                         result.leaderboard_stats.sharpe_ratio or 0.0,
                         result.leaderboard_stats.return_autocorrelation or 0.0)

    # ------------------------------------------------------------------ stages

    def _claim(self) -> Optional[_Pending]:
        logger.debug("Polling for work...")
        claimed = self.client.claim_next_interaction()
        if claimed is None:
            return None
        pending = _Pending(claimed=claimed)
        sub = claimed.submittedBot.name or f"bot#{claimed.submittedBot.id}"
        lead = claimed.leaderboardBot.name or f"bot#{claimed.leaderboardBot.id}"
        logger.info("Match %s claimed | %s vs %s | seed=%s | %d rounds",
                    pending.interaction_id, sub, lead,
                    claimed.interaction.seed, self.config.rounds)
        return pending

    def _execute(self, pending: _Pending) -> bool:
        """Run the match. Returns False if blocked by a harness fault."""
        interaction = pending.claimed.interaction

        if interaction.seed is None:
            # Cannot execute, and must not skip. Block until an operator fixes it.
            self._blocked(pending,
                          "server supplied no seed - this needs operator "
                          "intervention; retrying will not help by itself")
            return False

        # Bot code is passed through verbatim, including a poem. The container
        # fails to compile it, faults, and defaults to 0 for the whole match.
        # One code path for every kind of bad bot.
        submitted_code = pending.claimed.submittedBot.code or ""
        leaderboard_code = pending.claimed.leaderboardBot.code or ""

        start = time.perf_counter()
        try:
            result = run_match_result_from_code_strings(
                submitted_code=submitted_code,
                leaderboard_code=leaderboard_code,
                seed=interaction.seed,
                config=self.match_config,
                capture_history=True,
            )
        except MatchExecutionError as e:
            self._blocked(pending, str(e))
            return False
        except Exception as e:  # noqa: BLE001
            logger.exception("Unexpected harness failure on interaction %s",
                             pending.interaction_id)
            self._blocked(pending, f"{type(e).__name__}: {e}")
            return False

        elapsed = time.perf_counter() - start
        self._log_report(pending, result, elapsed)
        pending.result = result
        pending.submission = _to_submission(pending.interaction_id, result)
        return True

    def _submit(self, pending: _Pending) -> bool:
        try:
            updated = self.client.submit_result(pending.submission)
        except AuthenticationError:
            raise
        except NashiumClientError as e:
            self._blocked(pending,
                          f"could not submit the completed result: {e} "
                          f"(the match will NOT be re-run)")
            return False
        logger.info("Match %s submitted | status=%s result=%s",
                    updated.id, updated.status, updated.result)
        return True

    # -------------------------------------------------------------------- run

    def run_single(self) -> bool:
        """One claim/execute/submit cycle. Raises on harness failure (test mode)."""
        pending = self._claim()
        if pending is None:
            return False
        if not self._execute(pending):
            raise MatchExecutionError(
                f"Could not execute interaction {pending.interaction_id}")
        self._submit(pending)
        return True

    def run_forever(self) -> None:
        self._setup_signal_handlers()

        logger.info("=" * 74)
        logger.info("Nashium Worker starting")
        logger.info("  API URL          : %s", self.config.api_base_url)
        logger.info("  Execution        : Docker (sandboxed; the only supported mode)")
        logger.info("  Rounds per match : %d", self.config.rounds)
        logger.info("  CPU budget / bot : %.0fs (wall guard %.0fs)",
                    self.config.max_time_per_bot, self.config.max_wall_per_bot)
        logger.info("  RAM budget / bot : %s", _mb(self.config.max_memory_per_bot))
        logger.info("  Bot faults       : default to move 0, match still scored")
        logger.info("  Harness faults   : block and retry every %.0fs, never skip",
                    self.config.retry_interval_seconds)
        logger.info("=" * 74)

        while not self._stop.is_set():
            try:
                verify_environment()
                break
            except MatchExecutionError as e:
                logger.error("Docker preflight failed: %s", e)
                logger.error("Retrying preflight in %.0fs.",
                             self.config.retry_interval_seconds)
                reset_client()
                self._sleep(self.config.retry_interval_seconds)
        if self._stop.is_set():
            return

        cleanup_stale_containers()
        cleanup_stale_socket_dirs()

        while not self._stop.is_set():
            try:
                if self._pending is None:
                    self._pending = self._claim()
                    if self._pending is None:
                        self._sleep(self.config.idle_poll_interval_seconds)
                        continue

                pending = self._pending

                if pending.submission is None:
                    if not self._execute(pending):
                        self._sleep(self.config.retry_interval_seconds)
                        continue

                if not self._submit(pending):
                    self._sleep(self.config.retry_interval_seconds)
                    continue

                self._pending = None
                self._sleep(0.1)

            except AuthenticationError as e:
                logger.error("Authentication failed: %s", e)
                logger.error("The worker token is wrong or revoked. Stopping.")
                break

            except NashiumClientError as e:
                # The server being unreachable is never a reason to give up.
                logger.warning("Server unreachable (%s). Retrying in %.0fs.",
                               e, self.config.retry_interval_seconds)
                self._sleep(self.config.retry_interval_seconds)

            except Exception as e:  # noqa: BLE001
                logger.exception("Unexpected worker error: %s", e)
                self._sleep(self.config.retry_interval_seconds)

        if self._pending is not None:
            logger.warning("=" * 74)
            logger.warning("Stopping with interaction %s still incomplete.",
                           self._pending.interaction_id)
            logger.warning("It remains EXECUTING server-side and was NOT scored. "
                           "It will need requeueing or another worker.")
            logger.warning("=" * 74)

        cleanup_stale_containers()
        self.client.close()
        logger.info("Worker stopped")

    def stop(self) -> None:
        self._stop.set()