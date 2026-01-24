from __future__ import annotations

import hashlib


def stable_seed(a: bytes, b: bytes, secret: bytes = b"local") -> int:
    h = hashlib.sha256()
    h.update(secret)
    h.update(b"\x00")
    h.update(a)
    h.update(b"\x00")
    h.update(b)
    value = int.from_bytes(h.digest()[:8], "big", signed=False)
    return value & ((1 << 63) - 1)
