from __future__ import annotations

from ..core.engine import MatchConfig
from ..core.match_result import MatchResult
from ..server.docker import DockerConfig, run_match


def run_match_result_from_code_strings(
    submitted_code: str,
    leaderboard_code: str,
    *,
    seed: int,
    config: MatchConfig,
    capture_history: bool = True,
) -> MatchResult:
    """Run a match in Docker. Seeds are always supplied by the server."""
    if seed is None:
        raise ValueError("seed is required and must come from the server")

    return run_match(
        submitted_code=submitted_code,
        leaderboard_code=leaderboard_code,
        seed=seed,
        config=config,
        docker_config=DockerConfig(memory_limit=config.max_total_memory_bytes_per_bot),
        capture_history=capture_history,
    )