"""服务延伸候选池（Stage 41，需求第 3.3 节）。

「查订单完成 → 提示可查物流」类**服务型**延伸建议：与刚完成的任务高度相关、
非营销内容，在候选优先级阶梯中位于 P4（onboarding 之后、营销活动之前）。

配置为 JSON 文件（`FOLLOWUP_CONFIG_PATH`，示例 configs/followups.example.json），
campaigns 同模式：mtime 缓存自动重载，缺失/损坏一律空池（自然抑制）。
红线：suggest 话术走 i18n 模板键（确定性文案不经 LLM）；accept_intent 必须是
已注册技能的意图码——接受后要开出真实任务，未注册的建议本身就是坏配置，
加载时告警并跳过该条（需求第 5 节技术要求 3）。
"""

import json
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# mtime 缓存：{path: (mtime, followups)}
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def intent_registered(intent: str) -> bool:
    """意图码是否已注册技能（accept_intent 校验口径：接受后必须能开出任务）。"""
    if not intent:
        return False
    from app.chat.skills.registry import skill_registry

    return skill_registry.get(intent).intent == intent


def _validate(entry: dict[str, Any]) -> bool:
    """单条配置校验：必填字段齐全 + accept_intent 已注册（坏条目跳过不进池）。"""
    if not entry.get("followup_id") or not entry.get("suggest_key"):
        return False
    accept = entry.get("accept_intent")
    if accept and not intent_registered(str(accept)):
        logger.warning(
            "followup %s accept_intent %s not registered, skipped",
            entry.get("followup_id"), accept,
        )
        return False
    return True


def load_followups() -> list[dict[str, Any]]:
    """加载服务延伸池；未配置/缺失/损坏返回 []。"""
    path = settings.FOLLOWUP_CONFIG_PATH
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
        followups = [f for f in data if isinstance(f, dict) and _validate(f)]
    except Exception:  # noqa: BLE001 - 配置损坏=空池，不打断主链路
        logger.warning("followup config invalid: %s", p, exc_info=True)
        followups = []
    _cache[path] = (mtime, followups)
    return followups


def select_followup(intent: str) -> dict[str, Any] | None:
    """按本轮完成的意图选第一条匹配的服务延伸（顺序即优先级，campaigns 同款）。

    trigger_intents 支持完整意图码或 `DOMAIN.` 前缀；列表为空视为不匹配
    （必须显式声明面向哪些意图——「有延伸就推」与「有活动就推」同属禁区）。
    """
    for entry in load_followups():
        if not entry.get("enabled"):
            continue
        triggers = entry.get("trigger_intents") or []
        if any(
            intent == rule or (rule.endswith(".") and intent.startswith(rule))
            for rule in triggers
            if isinstance(rule, str)
        ):
            return entry
    return None
