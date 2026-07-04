"""ConfirmationResponseParser —— 确认门含糊应答的 LLM 解析（Stage 05）。

分工：短句确认/否认（「确认」「不用了」）由规则词表处理（rule_classifier 的
confirm gate，0ms）；本解析器只处理规则接不住的含糊表达：
- 「金额不对，应该是 200」→ MODIFY（带槽位修正，回填后重新进确认门）；
- 「行吧就这样弄吧」→ CONFIRM；
- 「等等我再想想」→ DENY；
- 「先帮我查下物流」→ UNRELATED（交回正常意图流程 → 任务挂起）。

未配置 API Key / LLM 失败返回 None（调用方维持原分类结果，行为不回退）。
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.chat.llm.factory import chat_completion, llm_available
from app.core.logging import get_logger

logger = get_logger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.S)

VERDICTS = ("CONFIRM", "DENY", "MODIFY", "UNRELATED")


@dataclass
class ParseOutcome:
    """解析结果。"""

    verdict: str  # CONFIRM / DENY / MODIFY / UNRELATED
    slot_updates: dict[str, Any] = field(default_factory=dict)


class ConfirmationResponseParser:
    """确认门应答解析器。"""

    async def parse(self, text: str, task: dict[str, Any]) -> ParseOutcome | None:
        """解析用户在 CONFIRMING 状态下的含糊应答；LLM 不可用/失败返回 None。"""
        if not llm_available():
            return None
        slots = task.get("collected_slots", {})
        slot_names = sorted(set(task.get("required_slots", [])) | set(slots))
        system = (
            "你是客服确认门的应答解析器。系统刚向用户复述了将要执行的操作并请求确认，"
            "请判断用户回复属于哪一类，只输出一个 JSON：\n"
            '{"verdict": "CONFIRM|DENY|MODIFY|UNRELATED", "slot_updates": {}}\n'
            "CONFIRM=同意执行；DENY=拒绝/暂缓；"
            "MODIFY=同意但要修正信息（把修正值放入 slot_updates，键只能取给定槽位名）；"
            "UNRELATED=在说别的事情。禁止输出 JSON 以外的内容。"
        )
        from app.chat.llm.prompt_guard import wrap_user_input

        user = (
            f"待确认操作：{task.get('intent')}，当前信息：{json.dumps(slots, ensure_ascii=False)}，"
            f"可修正的槽位名：{slot_names}\n用户回复：\n{wrap_user_input(text)}"
        )
        raw = await chat_completion(system, user, purpose="classify")
        if raw is None:
            return None
        m = _JSON_RE.search(raw)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            logger.warning("confirmation parser invalid json")
            return None
        verdict = str(data.get("verdict", "")).upper()
        if verdict not in VERDICTS:
            return None
        # 槽位修正白名单过滤：只接受任务声明过的槽位名
        updates = {
            k: str(v).strip()
            for k, v in (data.get("slot_updates") or {}).items()
            if k in slot_names and str(v).strip()
        }
        return ParseOutcome(verdict=verdict, slot_updates=updates)


# 模块级单例
confirmation_parser = ConfirmationResponseParser()
