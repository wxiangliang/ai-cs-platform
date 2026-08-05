"""活动池配置加载与资格判定（Stage 31，需求第 5 节）。

配置为 JSON 文件（`CAMPAIGN_CONFIG_PATH`，示例 configs/campaigns.example.json），
按 mtime 缓存自动重载（同 experiments 模式）。红线：hook 文案是运营写死的
确定性文本（含必要披露），不经 LLM 生成/改写——活动资格与金额没有编造空间。
缺失/损坏一律返回空活动池（无活动可推 = 自然抑制）。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# mtime 缓存：{path: (mtime, campaigns)}
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_campaigns() -> list[dict[str, Any]]:
    """加载活动池；未配置/缺失/损坏返回 []。"""
    path = settings.CAMPAIGN_CONFIG_PATH
    if not path:
        return []
    p = Path(path)
    if not p.is_absolute():
        p = _repo_root() / p
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        campaigns = [c for c in data if isinstance(c, dict) and c.get("campaign_id")]
    except Exception:  # noqa: BLE001 - 配置损坏=空池，不打断主链路
        logger.warning("campaign config invalid: %s", p, exc_info=True)
        campaigns = []
    _cache[path] = (mtime, campaigns)
    return campaigns


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def campaign_eligible(campaign: dict[str, Any], intent: str, now: datetime) -> bool:
    """单活动资格：启用 + 在有效期 + 与本轮完成的意图相关。

    相关性（需求第 4 节，防「有活动就推」）：eligible_intents 支持完整意图码
    或 `DOMAIN.` 前缀；列表为空视为不相关（必须显式声明面向哪些意图）。
    """
    if not campaign.get("enabled"):
        return False
    start = _parse_ts(campaign.get("valid_from"))
    end = _parse_ts(campaign.get("valid_to"))
    if start and now < start:
        return False
    if end and now > end:
        return False
    eligible = campaign.get("eligible_intents") or []
    return any(
        intent == rule or (rule.endswith(".") and intent.startswith(rule))
        for rule in eligible
        if isinstance(rule, str)
    )


def select_campaign(intent: str, now: datetime | None = None) -> dict[str, Any] | None:
    """按配置顺序选第一个符合资格的活动（v1 不排序，顺序即优先级）。"""
    now = now or datetime.now(timezone.utc)
    for campaign in load_campaigns():
        if campaign_eligible(campaign, intent, now):
            return campaign
    return None
