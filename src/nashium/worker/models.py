from __future__ import annotations

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

class SubMatchDTO(BaseModel):
    id: Optional[int] = None
    roundNumber: int
    seed: int
    penniesPerSubMatch: int
    roleA: str
    queuedAt: datetime
    completedAt: Optional[datetime] = None
    botAResult: Optional[str] = None
    botBResult: Optional[str] = None
    scoreA: Optional[int] = None
    scoreB: Optional[int] = None
    replayLog: Optional[str] = None
    nextStep: Optional[str] = None
    resolution: Optional[str] = None
    matchId: int

class MatchDTO(BaseModel):
    id: Optional[int] = None
    status: str
    isPractice: bool
    tier: Optional[str] = None
    wagerAmount: Optional[float] = None
    feeAmount: Optional[float] = None
    significanceLevel: Optional[str] = None
    targetPValue: Optional[float] = None
    actualPValue: Optional[float] = None
    glickoWeight: Optional[float] = None
    penniesPerSubMatch: Optional[int] = None
    minSubMatches: Optional[int] = None
    maxSubMatches: Optional[int] = None
    masterSeed: Optional[int] = None
    queuedAt: datetime
    startedAt: Optional[datetime] = None
    completedAt: Optional[datetime] = None
    cancellationReason: Optional[str] = None
    botASetWins: Optional[int] = None
    botBSetWins: Optional[int] = None
    setDraws: Optional[int] = None
    totalSubMatches: Optional[int] = None
    logBf: Optional[float] = None
    skillWeight: Optional[float] = None
    glickoScoreA: Optional[float] = None
    glickoScoreB: Optional[float] = None
    agencyAlphaDeltaA: Optional[float] = None
    agencyBetaDeltaA: Optional[float] = None
    agencyAlphaDeltaB: Optional[float] = None
    agencyBetaDeltaB: Optional[float] = None
    botAResult: Optional[str] = None
    botBResult: Optional[str] = None
    winnerBotId: Optional[int] = None
    winnerBotName: Optional[str] = None
    botASpoilsProtected: Optional[bool] = None
    botBSpoilsProtected: Optional[bool] = None
    spoilsEligible: bool
    spoilsGranted: bool
    userAPreRating: Optional[float] = None
    userAPreRd: Optional[float] = None
    userAPreVol: Optional[float] = None
    userBPreRating: Optional[float] = None
    userBPreRd: Optional[float] = None
    userBPreVol: Optional[float] = None
    userAPostRating: Optional[float] = None
    userAPostRd: Optional[float] = None
    userAPostVol: Optional[float] = None
    userBPostRating: Optional[float] = None
    userBPostRd: Optional[float] = None
    userBPostVol: Optional[float] = None
    userARatingDelta: Optional[float] = None
    userBRatingDelta: Optional[float] = None
    botAId: int
    botAName: Optional[str] = None
    botBId: int
    botBName: Optional[str] = None
    userAId: int
    userALogin: Optional[str] = None
    userBId: int
    userBLogin: Optional[str] = None
    entryAId: Optional[int] = None
    entryBId: Optional[int] = None

class BotDTO(BaseModel):
    id: Optional[int] = None
    name: str
    language: Optional[str] = None
    code: str
    status: str
    kind: str
    codePrivacyPurchased: bool
    createdAt: datetime
    lastEditedAt: Optional[datetime] = None
    finalizedAt: Optional[datetime] = None
    wins: int
    losses: int
    draws: int
    ownerId: int
    ownerLogin: Optional[str] = None

class NextQueuedInteractionDTO(BaseModel):
    subMatch: Optional[SubMatchDTO] = None
    match: Optional[MatchDTO] = None
    botA: Optional[BotDTO] = None
    botB: Optional[BotDTO] = None

class RuntimeStatsSubmissionDTO(BaseModel):
    timedOut: Optional[bool] = None
    memoryExceeded: Optional[bool] = None
    errored: Optional[bool] = None
    errorMessage: Optional[str] = None
    exitCode: Optional[int] = None
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
    subMatchId: Optional[int] = None
    botA: Optional[RuntimeStatsSubmissionDTO] = None
    botB: Optional[RuntimeStatsSubmissionDTO] = None