"""模板回复生成器。

根据「本轮处理状态 + Skill 模板 + 已收集槽位」选择模板并填充。
全部走模板，严格遵守护栏（不编造价格/退款结果）。
多语言（Stage 19）：skill 模板非默认语言查 catalog 覆盖，缺则回退 registry 中文
（zh 源，零回归）；安全兜底文案走 i18n。
"""

from typing import Any

from app.chat.skills.types import Skill
from app.chat.state.types import TurnStatus
from app.core.i18n import skill_template, t


class _SafeDict(dict):
    """格式化用：缺失的占位键返回空串，避免 KeyError。"""

    def __missing__(self, key: str) -> str:
        return ""


def render_reply(
    status: str,
    skill: Skill,
    collected_slots: dict[str, Any],
    locale: str | None = None,
) -> str:
    """根据状态选择模板并填充槽位生成回复文本。"""
    templates = skill.templates
    safe_slots = _SafeDict(collected_slots or {})

    def _tpl(key: str) -> str | None:
        # 语言覆盖优先，未覆盖回退 registry 中文模板
        return skill_template(skill.skill_id, key, locale) or templates.get(key)

    fallback = t("responder.safe_fallback", locale)
    if status == TurnStatus.NEEDS_SLOT:
        text = _tpl("collect") or fallback
    elif status == TurnStatus.NEEDS_CONFIRM:
        text = _tpl("confirm") or fallback
    elif status == TurnStatus.CONFIRMED:
        # 确认门通过：给受理回执（话术不得承诺已完成）
        text = _tpl("confirmed") or _tpl("answer") or fallback
    elif status == TurnStatus.FAILED:
        return t("responder.failed", locale)
    elif status == TurnStatus.FALLBACK:
        text = _tpl("answer") or fallback
    else:
        # DONE / HANDOFF / ABORTED 等：用 answer 模板
        text = _tpl("answer") or fallback

    # 用已收集槽位安全填充占位符
    return text.format_map(safe_slots)
