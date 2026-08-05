from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class JourneyStage(StrEnum):
    VISITOR = "VISITOR"
    NEW_USER = "NEW_USER"
    REGISTERING = "REGISTERING"
    REGISTERED = "REGISTERED"
    DISCOVERING = "DISCOVERING"
    CONSIDERING = "CONSIDERING"
    READY_TO_BUY = "READY_TO_BUY"
    PURCHASED = "PURCHASED"
    REPEAT_CUSTOMER = "REPEAT_CUSTOMER"
    AFTER_SALES = "AFTER_SALES"
    AT_RISK = "AT_RISK"


class JourneyTransition(BaseModel):
    from_stage: JourneyStage
    to_stage: JourneyStage
    confidence: float
    evidence_codes: list[str] = Field(default_factory=list)
    persist: bool = False
