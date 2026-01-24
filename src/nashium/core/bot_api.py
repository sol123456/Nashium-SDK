from __future__ import annotations

from typing import Protocol

from .engine import RoundState


class NashiumBot(Protocol):
    def move(self, state: RoundState) -> int: ...