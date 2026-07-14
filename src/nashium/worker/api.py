from __future__ import annotations

from nashium.core.engine import MatchConfig
from nashium.core.match_result import MatchResult
from nashium.core.util import stable_seed
from nashium.server.docker import DockerBackend


def run_match_result_from_code_strings(
        submitted_code: str,
        leaderboard_code: str,
        *,
        seed: int | None = None,
        config: MatchConfig | None = None,
        capture_history: bool = True,
) -> MatchResult:
    if config is None:
        config = MatchConfig()

    if seed is None:
        seed = stable_seed(submitted_code.encode("utf-8"), leaderboard_code.encode("utf-8"))

    # Pass strings directly to Docker via the backend
    backend = DockerBackend()
    return backend.run_match_result_from_strings(
        submitted_code,
        leaderboard_code,
        seed,
        config,
        capture_history=capture_history,
    )