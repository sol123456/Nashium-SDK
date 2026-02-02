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


# Default bot that always returns 0
DEFAULT_BOT_CODE = '''
"""Default bot - returns 0 for every move (used when actual bot code is invalid)."""

class Bot:
    def __init__(self, seed=None):
        pass
    
    def move(self, state):
        return 0

def create_bot(seed=None):
    return Bot(seed)
'''


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


def _is_valid_python(code: str) -> tuple[bool, Optional[str]]:
    """
    Check if code is syntactically valid Python.

    Returns:
        (True, None) if valid
        (False, error_message) if invalid
    """
    if not code or not code.strip():
        return False, "No code provided"
    try:
        compile(code, "<bot>", "exec")
        return True, None
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"


def _build_runtime_stats_dto(
    runtime: RuntimeStats,
    stats: Optional[BotStats],
    moves: tuple[int, ...],
    override_errored: bool = False,
    override_error_msg: Optional[str] = None,
) -> RuntimeStatsSubmissionDTO:
    """Convert SDK RuntimeStats + BotStats to the submission DTO."""

    # Use override if provided, otherwise use runtime's values
    errored = override_errored or runtime.errored
    error_msg = override_error_msg if override_errored else runtime.error_message

    return RuntimeStatsSubmissionDTO(
        timedOut=runtime.timed_out,
        memoryExceeded=runtime.memory_exceeded,
        errored=errored,
        errorMessage=error_msg,
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
    submitted_errored: bool = False,
    submitted_error_msg: Optional[str] = None,
    leaderboard_errored: bool = False,
    leaderboard_error_msg: Optional[str] = None,
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
            override_errored=submitted_errored,
            override_error_msg=submitted_error_msg,
        ),
        leaderboard=_build_runtime_stats_dto(
            result.leaderboard,
            result.leaderboard_stats,
            result.leaderboard_moves_effective,
            override_errored=leaderboard_errored,
            override_error_msg=leaderboard_error_msg,
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

        # 2. Get code, defaulting to empty string
        submitted_code = submitted_bot.code or ""
        leaderboard_code = leaderboard_bot.code or ""

        # Keep original code for seed generation
        original_submitted = submitted_code
        original_leaderboard = leaderboard_code

        # Track if we had to replace either bot
        submitted_errored = False
        leaderboard_errored = False
        submitted_error_msg: Optional[str] = None
        leaderboard_error_msg: Optional[str] = None

        # 3. Validate submitted bot code
        valid, error = _is_valid_python(submitted_code)
        if not valid:
            submitted_errored = True
            submitted_error_msg = error
            submitted_code = DEFAULT_BOT_CODE
            logger.warning(
                f"Submitted bot '{submitted_bot.name}' has invalid code: {error} "
                f"- replacing with default (all 0s)"
            )

        # 4. Validate leaderboard bot code
        valid, error = _is_valid_python(leaderboard_code)
        if not valid:
            leaderboard_errored = True
            leaderboard_error_msg = error
            leaderboard_code = DEFAULT_BOT_CODE
            logger.warning(
                f"Leaderboard bot '{leaderboard_bot.name}' has invalid code: {error} "
                f"- replacing with default (all 0s)"
            )

        # 5. Generate seed from ORIGINAL code (before any replacements)
        # This ensures the seed is consistent regardless of whether we replaced code
        seed = self._generate_seed(original_submitted, original_leaderboard)
        logger.info(f"Generated seed: {seed}")

        # 6. Run the match
        logger.info(f"Running match ({self.config.rounds} rounds)...")
        start_time = time.perf_counter()

        result = run_match_result_from_code_strings(
            submitted_code=submitted_code,
            leaderboard_code=leaderboard_code,
            seed=seed,
            config=self.match_config,
            sandbox=False,
            docker=True,
            capture_history=True,
        )

        elapsed = time.perf_counter() - start_time
        logger.info(
            f"Match completed in {elapsed:.2f}s: "
            f"result={result.result.value}, "
            f"submitted_wins={result.submitted_wins}/{result.rounds}"
        )

        # 7. Build submission, including any pre-run errors
        submission = _match_result_to_submission(
            interaction.id,
            result,
            submitted_errored=submitted_errored,
            submitted_error_msg=submitted_error_msg,
            leaderboard_errored=leaderboard_errored,
            leaderboard_error_msg=leaderboard_error_msg,
        )

        # 8. Submit results
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