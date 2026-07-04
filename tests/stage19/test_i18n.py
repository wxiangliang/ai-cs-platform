"""多语言地基单元测试（Stage 19）。"""

import uuid

import pytest

from app.chat.graph.nodes.load_session_state import load_session_state
from app.chat.skills.registry import skill_registry
from app.chat.skills.responder import render_reply
from app.core.i18n import resolve_locale, skill_template, t
from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.chat_session_repository import chat_session_repository

TENANT = "i18n-t"


# --- t() 查表与回退 ---


def test_t_default_zh_exact():
    """默认语言返回中文源文案（逐字，零回归锚点）。"""
    assert t("responder.failed") == "抱歉，我暂时无法处理这条消息，请换个说法或稍后再试。"
    assert t("csat.thanks_high") == "感谢您的评价！有任何问题随时找我。"


def test_t_english_when_present():
    assert t("guardrail.injection", "en").startswith("Sorry")


def test_t_falls_back_to_default_when_key_missing_in_locale():
    """en 未补的 key → 回退中文（不空串）。"""
    assert t("task.gave_up", "en") == t("task.gave_up", "zh")


def test_t_unknown_locale_falls_back():
    assert t("responder.failed", "ja") == t("responder.failed", "zh")


def test_t_unknown_key_returns_key():
    assert t("no.such.key") == "no.such.key"


def test_t_param_interpolation():
    assert t("handoff.queue_note", "zh", position=5) == "，当前排队第 5 位"
    # 缺参数不崩（_SafeDict 返回空串）
    assert "{" not in t("product.not_found", "zh")


def test_resolve_locale():
    assert resolve_locale("en") == "en"
    assert resolve_locale("zh") == "zh"
    assert resolve_locale("ja") == "zh"  # 不支持回退默认
    assert resolve_locale(None) == "zh"
    assert resolve_locale("") == "zh"


# --- skill 模板语言覆盖 ---


def test_skill_template_override():
    assert skill_template("aftersale_refund", "collect", "zh") is None  # 默认语言不覆盖
    assert skill_template("aftersale_refund", "collect", "en").startswith("Sure")
    assert skill_template("aftersale_refund", "collect", "ja") is None  # 无覆盖


def test_render_reply_zero_regression_and_override():
    sk = skill_registry.get("AFTERSALE.REFUND")
    zh = render_reply("NEEDS_SLOT", sk, {})
    en = render_reply("NEEDS_SLOT", sk, {}, "en")
    # zh 走 registry 中文（零回归）；en 走 catalog 覆盖
    assert zh == sk.templates["collect"]
    assert en != zh and "refund" in en.lower()
    # FAILED 兜底走 i18n
    assert render_reply("FAILED", sk, {}) == t("responder.failed", "zh")
    assert render_reply("FAILED", sk, {}, "en") == t("responder.failed", "en")


def test_render_reply_slot_fill_unchanged():
    """占位符填充行为不变（{product_name}）。"""
    sk = skill_registry.get("PRODUCT.ASK_PRICE")
    out = render_reply("FALLBACK", sk, {"product_name": "凉风空调X1"})
    assert "凉风空调X1" in out


# --- LLM prompt 含语言指示 ---


def test_prompts_have_language_instruction():
    from app.chat.llm import prompts
    from app.kb import answerer

    _, _ = prompts.build_reply_polish_prompt("底稿", "问题", history=[])
    sys_polish, _ = prompts.build_reply_polish_prompt("底稿", "问题", history=[])
    assert "语言" in sys_polish and "一致" in sys_polish
    assert "语言" in answerer._RAG_SYSTEM_PROMPT


# --- locale 贯穿：请求 → 会话记忆 → 后续轮沿用 ---


@pytest.fixture
async def _session():
    user_id = f"u-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as s:
        rec = await chat_session_repository.create(
            s, tenant_id=TENANT, user_id=user_id, channel="web"
        )
        sid = rec.id
        await s.commit()
    yield user_id, sid
    await dispose_engine()


async def test_locale_decided_and_remembered(_session):
    user_id, sid = _session
    config_key = {"configurable": None}
    async with AsyncSessionLocal() as session:
        config_key["configurable"] = {"db_session": session}
        # 首轮带 locale=en → state.locale=en 且记入会话
        state = {"tenant_id": TENANT, "session_id": sid, "user_id": user_id,
                 "message": "hi", "locale": "zh", "locale_req": "en"}
        out = await load_session_state(state, config_key)
        assert out["locale"] == "en"
        await session.commit()
        rec = await chat_session_repository.get_by_id(session, sid)
        assert (rec.metadata_json or {}).get("locale") == "en"

    async with AsyncSessionLocal() as session:
        config_key["configurable"] = {"db_session": session}
        # 次轮不带 locale → 沿用会话记住的 en
        state = {"tenant_id": TENANT, "session_id": sid, "user_id": user_id,
                 "message": "hello", "locale": "zh", "locale_req": None}
        out = await load_session_state(state, config_key)
        assert out["locale"] == "en"


# --- 请求 schema 有 locale 字段 ---


def test_request_schema_locale():
    from app.schemas.chat import ChatMessageRequest

    r = ChatMessageRequest(user_id="u", message="hi", locale="en")
    assert r.locale == "en"
    assert ChatMessageRequest(user_id="u", message="hi").locale is None
