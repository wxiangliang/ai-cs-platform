from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class FrequencyState:
    impression_count: int
    last_impression_at: datetime | None
    opted_out: bool = False


@dataclass(frozen=True)
class FrequencyDecision:
    allowed: bool
    reason_codes: tuple[str, ...]


def evaluate_frequency_cap(
    state: FrequencyState,
    *,
    max_impressions: int,
    cooldown_hours: int,
    now: datetime | None = None,
) -> FrequencyDecision:
    current = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    if state.opted_out:
        reasons.append("user_opted_out")

    if state.impression_count >= max_impressions:
        reasons.append("max_impressions_reached")

    if state.last_impression_at is not None:
        next_allowed_at = state.last_impression_at + timedelta(hours=cooldown_hours)
        if current < next_allowed_at:
            reasons.append("campaign_cooldown_active")

    return FrequencyDecision(
        allowed=not reasons,
        reason_codes=tuple(reasons),
    )
