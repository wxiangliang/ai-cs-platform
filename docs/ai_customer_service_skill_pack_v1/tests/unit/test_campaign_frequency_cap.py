from datetime import datetime, timedelta, timezone

from app.chat.campaigns.frequency_cap import (
    FrequencyState,
    evaluate_frequency_cap,
)


def test_opt_out_blocks_campaign() -> None:
    result = evaluate_frequency_cap(
        FrequencyState(impression_count=0, last_impression_at=None, opted_out=True),
        max_impressions=2,
        cooldown_hours=48,
    )
    assert result.allowed is False
    assert "user_opted_out" in result.reason_codes


def test_cooldown_blocks_recent_impression() -> None:
    now = datetime.now(timezone.utc)
    result = evaluate_frequency_cap(
        FrequencyState(
            impression_count=1,
            last_impression_at=now - timedelta(hours=2),
        ),
        max_impressions=2,
        cooldown_hours=48,
        now=now,
    )
    assert result.allowed is False
    assert "campaign_cooldown_active" in result.reason_codes
