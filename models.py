from __future__ import annotations

import random
import time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Decision(str, Enum):
    ACCEPT = "ACCEPT"
    DENY = "DENY"


class GameMode(str, Enum):
    SINGLE_PLAYER = "single"
    MULTI_PLAYER = "multiplayer"


class FraudAssessment(str, Enum):
    NOT_MALICIOUS = "not_malicious"
    SUSPICIOUS = "suspicious"
    FLAGGED = "flagged"


class SpendingPotential(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Customer(BaseModel):
    id: str
    name: str
    photo_included: bool
    total_purchases: int
    total_returns: int
    return_rate: float
    total_purchase_amount: float
    total_return_amount: float
    return_dollar_ratio: float
    current_return_item: str
    current_return_amount: float
    loyalty_tier: str  # GOLD, SILVER, BRONZE, NONE — from BigQuery
    fraud_assessment: FraudAssessment
    spending_potential: SpendingPotential
    correct_decision: Decision


class DecisionRequest(BaseModel):
    customer_id: str
    decision: Decision


class DecisionResponse(BaseModel):
    correct: bool
    explanation: str


class SessionCreate(BaseModel):
    name: str


class SessionDecision(BaseModel):
    customer_id: str
    decision: Decision
    correct: bool
    time_taken: float


class Session(BaseModel):
    id: str
    name: str
    mode: GameMode = GameMode.SINGLE_PLAYER
    challenge_code: Optional[str] = None
    agent_token_hash: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    duration_minutes: int = 20
    started_at: Optional[float] = None
    expires_at: Optional[float] = None
    decisions: list[SessionDecision] = Field(default_factory=list)
    customer_indices: list[int] = Field(default_factory=list)
    current_customer_index: int = 0
    current_customer_id: Optional[str] = None
    current_customer_started_at: Optional[float] = None

    def ensure_started(self, now: Optional[float] = None) -> None:
        if self.started_at is not None:
            return
        now = now or time.time()
        self.started_at = now
        self.expires_at = now + self.duration_minutes * 60

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or time.time()
        return now >= self.expires_at

    def remaining_seconds(self, now: Optional[float] = None) -> int:
        if self.started_at is None:
            return self.duration_minutes * 60
        if self.expires_at is None:
            return self.duration_minutes * 60
        now = now or time.time()
        return max(0, int(self.expires_at - now))

    def ensure_deck(self, pool_size: int, deck_size: int) -> None:
        if self.customer_indices:
            return
        if pool_size <= 0 or deck_size <= 0:
            return

        rng = random.Random(self.id)
        n = min(deck_size, pool_size)
        self.customer_indices = rng.sample(range(pool_size), n)

    @property
    def is_complete(self) -> bool:
        return (
            len(self.customer_indices) > 0
            and self.current_customer_index >= len(self.customer_indices)
        )

    @property
    def state(self) -> str:
        if self.started_at is None:
            return "ready"
        if self.is_expired() or self.is_complete:
            return "ended"
        return "active"

    @property
    def correct_count(self) -> int:
        return sum(1 for d in self.decisions if d.correct)

    @property
    def total_count(self) -> int:
        return len(self.decisions)

    @property
    def accuracy(self) -> float:
        if not self.decisions:
            return 0.0
        return self.correct_count / self.total_count

    @property
    def avg_time(self) -> float:
        if not self.decisions:
            return 0.0
        return sum(d.time_taken for d in self.decisions) / len(self.decisions)

    @property
    def score(self) -> float:
        if not self.decisions or self.avg_time == 0:
            return 0.0
        return self.correct_count * (100 / self.avg_time)


class LeaderboardEntry(BaseModel):
    rank: int
    session_id: str
    name: str
    state: str
    correct: int
    total: int
    accuracy: float
    avg_time: float
    score: float
    started_at: float
    in_week: bool
    section: str
