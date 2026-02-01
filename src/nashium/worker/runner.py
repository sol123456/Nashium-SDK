"""
Main worker loop - claims matches, runs them, submits results.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from dataclasses import dataclass
from typing import Optional

from .client import NashiumClient, NashiumClientError, AuthenticationError
from .models import (
    NextQueuedInteractionDTO,
    MatchResultSubmissionDTO,
    RuntimeStatsSubmissionDTO,
)
from .seed import generate_match_seed

# Import from the SDK core
from ..core.engine import MatchConfig
from ..core.match_result import MatchResult, RuntimeStats, BotStats
from .api import run_match_result_from_code_strings

logger = logging.getLogger(__name__)


@dataclass
class WorkerConfig:
    """Configuration for the worker."""

    # API connection
    api_base_url: str = "http://localhost:8080"
    worker_token: str = ""

    # Seed generation secret (REQUIRED - must match server config)
    seed_secret: str = ""

    # Match config
    rounds: int = 10_000
    stat_sig_win_threshold: int = 5155
    max_time_per_bot: float = 100.0
    max_memory_per_bot: Optional[int] = None

    # Worker behavior
    poll_interval_seconds: float = 2.0
    idle_poll_interval_seconds: float = 10.0
    max_consecutive_errors: int = 5
    error_backoff_seconds: float = 30.0

    # Logging
    log_level: str = "INFO"


def _build_runtime_stats_dto(
    runtime: RuntimeStats,
    stats: Optional[BotStats],
    moves: tuple[int, ...],
) -> RuntimeStatsSubmissionDTO:
    """Convert SDK RuntimeStats + BotStats to the submission DTO."""

    return RuntimeStatsSubmissionDTO(
        timedOut=runtime.timed_out,
        memoryExceeded=runtime.memory_exceeded,
        errored=runtime.errored,
        errorMessage=runtime.error_message,
        maxMemory=float(runtime.memory_bytes_peak) if runtime.memory_bytes_peak else None,
        endCpuTime=runtime.elapsed_time_seconds,
        cpuUsageSamples=list(runtime.cpu_usage_samples) if runtime.cpu_usage_samples else None,
        ramUsageSamples=list(runtime.ram_usage_samples) if runtime.ram_usage_samples else None,
        moves=list(moves) if moves else None,
        wins=stats.wins if stats else None,
        losses=stats.losses if stats else None,
        entropy=stats.entropy if stats else None,
        sharpeRatio=stats.sharpe_ratio if stats else None,
        returnAutocorrelation=stats.return_autocorrelation if stats else None,
    )


def _match_result_to_submission(
    interaction_id: int,
    result: MatchResult,
) -> MatchResultSubmissionDTO:
    """Convert SDK MatchResult to the API submission DTO."""

    return MatchResultSubmissionDTO(
        interactionId=interaction_id,
        seed=result.seed,
        result=result.result.value,
        submitted=_build_runtime_stats_dto(
            result.submitted,
            result.submitted_stats,
            result.submitted_moves,
        ),
        leaderboard=_build_runtime_stats_dto(
            result.leaderboard,
            result.leaderboard_stats,
            result.leaderboard_moves_effective,
        ),
    )


class Worker:
    """
    Main worker class - runs the poll/execute/submit loop.

    Execution settings (hardcoded for security):
    - sandbox=False (we use Docker instead)
    - docker=True (containerized execution)
    - capture_history=True (we need moves for stats)
    """

    def __init__(self, config: WorkerConfig):
        self.config = config
        self.client = NashiumClient(
            base_url=config.api_base_url,
            worker_token=config.worker_token,
        )
        self.match_config = MatchConfig(
            rounds=config.rounds,
            stat_sig_win_threshold=config.stat_sig_win_threshold,
            max_total_time_seconds_per_bot=config.max_time_per_bot,
            max_total_memory_bytes_per_bot=config.max_memory_per_bot,
        )

        self._running = False
        self._consecutive_errors = 0

        # Validate required config
        if not config.seed_secret:
            raise ValueError("seed_secret is required in WorkerConfig")

        # Setup logging
        logging.basicConfig(
            level=getattr(logging, config.log_level.upper()),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers."""
        def handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            self._running = False

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def _generate_seed(self, submitted_code: str, leaderboard_code: str) -> int:
        """Generate a secure deterministic seed for this match."""
        return generate_match_seed(
            submitted_code=submitted_code,
            leaderboard_code=leaderboard_code,
            secret_key=self.config.seed_secret,
        )

    def run_single(self) -> bool:
        """
        Run a single poll/execute/submit cycle.

        Returns:
            True if work was processed, False if no work available.
        """
        # 1. Claim next interaction
        logger.debug("Polling for work...")
        interaction_data = self.client.claim_next_interaction()

        if interaction_data is None:
            logger.debug("No work available")
            return False

        interaction = interaction_data.interaction
        submitted_bot = interaction_data.submittedBot
        leaderboard_bot = interaction_data.leaderboardBot

        logger.info(
            f"Claimed interaction {interaction.id}: "
            f"{submitted_bot.name} vs {leaderboard_bot.name}"
        )

        # 2. Validate we have code
        if not submitted_bot.code:
            logger.error(f"Submitted bot {submitted_bot.id} has no code!")
            raise NashiumClientError("Submitted bot has no source code")

        if not leaderboard_bot.code:
            logger.error(f"Leaderboard bot {leaderboard_bot.id} has no code!")
            raise NashiumClientError("Leaderboard bot has no source code")

        # 3. Generate secure deterministic seed
        seed = self._generate_seed(submitted_bot.code, leaderboard_bot.code)
        logger.info(f"Generated seed: {seed}")

        # 4. Run the match
        logger.info(f"Running match ({self.config.rounds} rounds)...")
        start_time = time.perf_counter()

        try:
            result = run_match_result_from_code_strings(
                submitted_code=submitted_bot.code,
                leaderboard_code=leaderboard_bot.code,
                seed=seed,
                config=self.match_config,
                sandbox=False,      # Not using Python sandbox
                docker=True,        # Using Docker for isolation
                capture_history=True,  # Need moves for statistics
            )
        except Exception as e:
            logger.error(f"Match execution failed: {e}")
            raise

        elapsed = time.perf_counter() - start_time
        logger.info(
            f"Match completed in {elapsed:.2f}s: "
            f"result={result.result.value}, "
            f"submitted_wins={result.submitted_wins}/{result.rounds}"
        )

        # 5. Convert to submission DTO
        submission = _match_result_to_submission(interaction.id, result)

        # 6. Submit results
        logger.info(f"Submitting results for interaction {interaction.id}...")
        updated = self.client.submit_result(submission)

        logger.info(
            f"Successfully submitted interaction {updated.id}, "
            f"status: {updated.status}, result: {updated.result}"
        )

        return True

    def run_forever(self):
        """
        Run the worker loop indefinitely until stopped.
        """
        self._running = True
        self._setup_signal_handlers()

        logger.info("=" * 60)
        logger.info("Nashium Worker starting")
        logger.info(f"  API URL: {self.config.api_base_url}")
        logger.info(f"  Execution: Docker (sandboxed)")
        logger.info(f"  Rounds per match: {self.config.rounds}")
        logger.info(f"  Max time per bot: {self.config.max_time_per_bot}s")
        logger.info("=" * 60)

        # Health check
        if not self.client.health_check():
            logger.error(f"Cannot reach server at {self.config.api_base_url}")
            logger.error("Please check the API URL and ensure the server is running")
            sys.exit(1)

        logger.info("Server connection verified")

        while self._running:
            try:
                did_work = self.run_single()
                self._consecutive_errors = 0

                if did_work:
                    # Immediately check for more work
                    time.sleep(0.1)
                else:
                    # No work - use longer idle interval
                    logger.debug(
                        f"Sleeping {self.config.idle_poll_interval_seconds}s (idle)..."
                    )
                    time.sleep(self.config.idle_poll_interval_seconds)

            except AuthenticationError as e:
                logger.error(f"Authentication failed: {e}")
                logger.error("Please check your worker token")
                self._running = False
                sys.exit(1)

            except NashiumClientError as e:
                self._consecutive_errors += 1
                logger.error(f"Client error ({self._consecutive_errors}): {e}")

                if self._consecutive_errors >= self.config.max_consecutive_errors:
                    logger.error(
                        f"Too many consecutive errors "
                        f"({self._consecutive_errors}), stopping"
                    )
                    self._running = False
                    sys.exit(1)

                logger.info(f"Backing off for {self.config.error_backoff_seconds}s...")
                time.sleep(self.config.error_backoff_seconds)

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                self._running = False

            except Exception as e:
                self._consecutive_errors += 1
                logger.exception(f"Unexpected error ({self._consecutive_errors}): {e}")

                if self._consecutive_errors >= self.config.max_consecutive_errors:
                    logger.error("Too many errors, stopping")
                    self._running = False
                    sys.exit(1)

                time.sleep(self.config.error_backoff_seconds)

        logger.info("Worker stopped")
        self.client.close()

    def stop(self):
        """Signal the worker to stop."""
        self._running = False