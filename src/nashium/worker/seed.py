"""
Cryptographically secure deterministic seed generation.

The seed is:
- Deterministic: Same inputs always produce the same seed
- Unguessable: Cannot be predicted without knowing the secret key
- Collision-resistant: Different bot pairs produce different seeds
"""

from __future__ import annotations

import hmac
import hashlib


def generate_match_seed(
        submitted_code: str,
        leaderboard_code: str,
        secret_key: str,
) -> int:
    """
    Generate a deterministic but unguessable seed for a match.

    Uses HMAC-SHA256 to create a seed that:
    - Is fully deterministic given the same inputs
    - Cannot be predicted without the secret key
    - Changes completely if any input changes

    Args:
        submitted_code: Source code of the submitted bot
        leaderboard_code: Source code of the leaderboard bot
        secret_key: Server-side secret (must be kept private!)

    Returns:
        A 32-bit integer seed suitable for random number generation.
    """
    if not secret_key:
        raise ValueError("Secret key is required for seed generation")

    # Combine bot codes with a separator that can't appear in code
    # Using null byte as separator since it's invalid in Python source
    message = f"{submitted_code}\x00{leaderboard_code}".encode("utf-8")
    key = secret_key.encode("utf-8")

    # HMAC-SHA256 - cryptographically secure
    h = hmac.new(key, message, hashlib.sha256)
    digest = h.digest()

    # Use first 4 bytes as seed (32-bit integer)
    # This gives us 2^32 possible seeds, which is plenty
    seed = int.from_bytes(digest[:4], byteorder="big", signed=False)

    return seed


def generate_match_seed_with_salt(
        submitted_code: str,
        leaderboard_code: str,
        secret_key: str,
        salt: str = "",
) -> int:
    """
    Generate a seed with an additional salt.

    Useful if you need different seeds for the same bot pair
    (e.g., for rematches or multiple rounds).

    Args:
        submitted_code: Source code of the submitted bot
        leaderboard_code: Source code of the leaderboard bot
        secret_key: Server-side secret
        salt: Additional entropy (e.g., interaction ID, timestamp)

    Returns:
        A 32-bit integer seed.
    """
    if not secret_key:
        raise ValueError("Secret key is required for seed generation")

    # Include salt in the message
    message = f"{submitted_code}\x00{leaderboard_code}\x00{salt}".encode("utf-8")
    key = secret_key.encode("utf-8")

    h = hmac.new(key, message, hashlib.sha256)
    digest = h.digest()

    return int.from_bytes(digest[:4], byteorder="big", signed=False)