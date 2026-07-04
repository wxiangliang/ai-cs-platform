"""国际化查表（Stage 19）。

面向用户的确定性文案统一经 `t(key, locale, **params)` 取——按 locale 查语言包，
缺失回退 `LOCALE_DEFAULT`，再缺返回 key 本身并告警（绝不崩/不空串）。
LLM 生成的回复不走这里：模型按用户语言直接回复（见 prompts 的语言指示）。
"""

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.locales import LOCALES

logger = get_logger(__name__)


class _SafeDict(dict):
    """format_map 用：缺失占位键返回空串，避免 KeyError。"""

    def __missing__(self, key: str) -> str:
        return ""


def resolve_locale(locale: str | None) -> str:
    """归一 locale：不在支持列表则回退默认（防脏输入）。"""
    supported = {x.strip() for x in settings.SUPPORTED_LOCALES.split(",") if x.strip()}
    if locale and locale in supported:
        return locale
    return settings.LOCALE_DEFAULT


def t(key: str, locale: str | None = None, **params: Any) -> str:
    """取文案：locale 缺该 key → 回退默认语言 → 回退 key 本身（告警）。"""
    loc = resolve_locale(locale)
    template = (LOCALES.get(loc) or {}).get(key)
    if template is None and loc != settings.LOCALE_DEFAULT:
        template = (LOCALES.get(settings.LOCALE_DEFAULT) or {}).get(key)
    if template is None:
        logger.warning("i18n missing key: %s (locale=%s)", key, loc)
        return key
    return template.format_map(_SafeDict(params))


def skill_template(skill_id: str, template_key: str, locale: str | None) -> str | None:
    """skill 模板的语言覆盖：非默认语言查 `skill.<id>.<key>`；

    命中返回目标语言文案，未命中返回 None——调用方（responder）回退到
    registry 里的中文模板（zh 源，零回归）。
    """
    loc = resolve_locale(locale)
    if loc == settings.LOCALE_DEFAULT:
        return None
    return (LOCALES.get(loc) or {}).get(f"skill.{skill_id}.{template_key}")
