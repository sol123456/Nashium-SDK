"""
Pydantic models matching JHipster DTOs for API communication.
"""

from __future__ import annotations

from typing import Optional, List, Any
from pydantic import BaseModel, Field


class BotDTO(BaseModel):
    """Matches com.nashium.myapp.service.dto.BotDTO"""
    id: int
    name: Optional[str] = None
    code: Optional[str] = None
    language: Optional[str] = None
    version: Optional[int] = None

    # Add other fields as needed from your BotDTO

    class Config:
        extra = "ignore"  # Ignore extra fields from API


class InteractionDTO(BaseModel):
    """Matches com.nashium.myapp.service.dto.InteractionDTO"""
    id: int
    status: Optional[str] = None
    result: Optional[str] = None
    seed: Optional[int] = None
    queuedAt: Optional[str] = None
    completedAt: Optional[str] = None
    submittedBotId: Optional[int] = None
    leaderboardBotId: Optional[int] = None

    class Config:
        extra = "ignore"


class NextQueuedInteractionDTO(BaseModel):
    """Matches com.nashium.myapp.service.dto.NextQueuedInteractionDTO"""
    interaction: InteractionDTO
    submittedBot: BotDTO
    leaderboardBot: BotDTO
    hasExecutingInteraction: bool = False
    warningMessage: Optional[str] = None

    class Config:
        extra = "ignore"


class RuntimeStatsSubmissionDTO(BaseModel):
    """Matches com.nashium.myapp.service.dto.workerDTOs.RuntimeStatsSubmissionDTO"""
    timedOut: Optional[bool] = None
    memoryExceeded: Optional[bool] = None
    errored: Optional[bool] = None
    errorMessage: Optional[str] = None

    maxMemory: Optional[float] = None
    endCpuTime: Optional[float] = None

    cpuUsageSamples: Optional[List[int]] = None
    ramUsageSamples: Optional[List[int]] = None
    moves: Optional[List[int]] = None
    performance: Optional[List[int]] = None

    wins: Optional[int] = None
    losses: Optional[int] = None
    entropy: Optional[float] = None
    sharpeRatio: Optional[float] = None
    returnAutocorrelation: Optional[float] = None


class MatchResultSubmissionDTO(BaseModel):
    """Matches com.nashium.myapp.service.dto.workerDTOs.MatchResultSubmissionDTO"""
    interactionId: int
    seed: int
    result: str  # "S_LOSS", "S_WIN", "DRAW", "STAT_DRAW_S_WIN", "STAT_DRAW_S_LOSS"
    submitted: RuntimeStatsSubmissionDTO
    leaderboard: RuntimeStatsSubmissionDTO