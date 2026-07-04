"""LLM 槽位抽取兜底（Stage 04-01）。

触发条件（slot_extract 节点判断）：意图的必填槽位在规则抽取后仍缺失、
文本非纯槽位输入、且 LLM 可用。
合并规则：**规则结果优先**（正则命中即信任），LLM 只补空缺，不覆盖；
LLM 输出经白名单槽位名过滤与字符串化，禁止引入未声明的键。
"""

import json
import re
from typing import Any

from app.chat.llm.factory import chat_completion, llm_available
from app.chat.llm.prompts import build_slot_extraction_prompt
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.S)


async def extract_missing_slots(
    text: str, intent: str, missing_slots: list[str]
) -> dict[str, Any]:
    """用 LLM 补抽缺失槽位；不可用/失败/解析异常一律返回空 dict（无害降级）。"""
    if not (settings.LLM_SLOT_FALLBACK and llm_available() and missing_slots):
        return {}
    system, user = build_slot_extraction_prompt(text, intent, missing_slots)
    raw = await chat_completion(system, user, purpose="classify")
    if raw is None:
        return {}
    m = _JSON_RE.search(raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        logger.warning("llm slot extraction returned invalid json")
        return {}
    if not isinstance(data, dict):
        return {}
    # 白名单过滤 + 值字符串化：只接受声明过的槽位名，丢弃空值
    result: dict[str, Any] = {}
    for name in missing_slots:
        value = data.get(name)
        if value is None:
            continue
        value_str = str(value).strip()
        if value_str:
            result[name] = value_str
    return result
