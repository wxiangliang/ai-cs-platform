"""可观测与评估平台单元测试（Stage 09）。"""

import sys
import uuid
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from app.core import metrics as m
from app.core.exceptions import AppException
from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.schemas.chat import FeedbackRequest
from app.services.chat_service import chat_service

TENANT = "obs-t"


def _sample(name: str, labels: dict[str, str]) -> float:
    """读当前计数（不存在视为 0）。"""
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ---------------------------------------------------------------------------
# 指标计数正确性（before/after 断言）
# ---------------------------------------------------------------------------


def test_intent_counter():
    labels = {"pred_label": "ORDER.QUERY_STATUS", "decision_source": "SETFIT"}
    before = _sample("intent_decisions_total", labels)
    m.count_intent("ORDER.QUERY_STATUS", "SETFIT")
    assert _sample("intent_decisions_total", labels) == before + 1


def test_turn_histogram_uses_domain():
    labels = {"intent_domain": "AFTERSALE", "branch": "action_executor"}
    before = _sample("chat_turn_duration_seconds_count", labels)
    m.observe_turn("AFTERSALE.REFUND", "action_executor", 0.12)
    assert _sample("chat_turn_duration_seconds_count", labels) == before + 1
    # 空意图归 UNKNOWN 域、空分支归 template
    m.observe_turn(None, None, 0.01)
    assert _sample(
        "chat_turn_duration_seconds_count", {"intent_domain": "UNKNOWN", "branch": "template"}
    ) >= 1


def test_action_tool_whitelist_caps_cardinality():
    before = _sample("action_executions_total", {"tool_id": "other", "ok": "true"})
    m.count_action("some_unknown_tool_xyz", ok=True)  # 白名单外 → other
    assert _sample("action_executions_total", {"tool_id": "other", "ok": "true"}) == before + 1


def test_llm_failure_counts_both():
    calls = _sample("llm_calls_total", {"purpose": "classify"})
    fails = _sample("llm_failures_total", {"purpose": "classify"})
    m.count_llm_call("classify", ok=False)
    assert _sample("llm_calls_total", {"purpose": "classify"}) == calls + 1
    assert _sample("llm_failures_total", {"purpose": "classify"}) == fails + 1


def test_render_metrics_exposition():
    payload, content_type = m.render_metrics()
    assert b"intent_decisions_total" in payload
    assert "text/plain" in content_type


# ---------------------------------------------------------------------------
# 反馈归属校验与幂等
# ---------------------------------------------------------------------------


@pytest.fixture
async def _seeded():
    """一个会话 + 一条 AI 回复消息。"""
    user_id = f"obs-u-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as session:
        record = await chat_session_repository.create(
            session, tenant_id=TENANT, user_id=user_id, channel="web"
        )
        sid = record.id
        ai = await chat_message_repository.create(
            session, tenant_id=TENANT, session_id=sid, role="assistant", content="答复"
        )
        um = await chat_message_repository.create(
            session, tenant_id=TENANT, session_id=sid, role="user", content="问题"
        )
        await session.commit()
        ids = (sid, user_id, ai.id, um.id)
    yield ids
    await dispose_engine()


async def test_feedback_success_and_idempotent(_seeded):
    sid, user_id, ai_id, _ = _seeded
    async with AsyncSessionLocal() as session:
        req = FeedbackRequest(user_id=user_id, message_id=ai_id, rating="down", comment="没答对")
        data = await chat_service.submit_feedback(session, sid, req, TENANT)
        assert data["rating"] == "down"
        # 重复评价同一消息 → 更新不报错（幂等）
        req2 = FeedbackRequest(user_id=user_id, message_id=ai_id, rating="up")
        data2 = await chat_service.submit_feedback(session, sid, req2, TENANT)
        await session.commit()
        assert data2["feedback_id"] == data["feedback_id"] and data2["rating"] == "up"


async def test_feedback_rejects_wrong_owner(_seeded):
    sid, _, ai_id, _ = _seeded
    async with AsyncSessionLocal() as session:
        req = FeedbackRequest(user_id="别人", message_id=ai_id, rating="up")
        with pytest.raises(AppException) as exc:
            await chat_service.submit_feedback(session, sid, req, TENANT)
        assert exc.value.error_code == "SESSION_NOT_FOUND"


async def test_feedback_rejects_user_message(_seeded):
    """只有 AI/坐席回复可评价（role=user 拒绝，防投毒）。"""
    sid, user_id, _, um_id = _seeded
    async with AsyncSessionLocal() as session:
        req = FeedbackRequest(user_id=user_id, message_id=um_id, rating="down")
        with pytest.raises(AppException) as exc:
            await chat_service.submit_feedback(session, sid, req, TENANT)
        assert exc.value.error_code == "MESSAGE_NOT_FOUND"


def test_feedback_rating_validation():
    with pytest.raises(ValueError):
        FeedbackRequest(user_id="u", message_id="m", rating="great")


# ---------------------------------------------------------------------------
# 导出脚本：脱敏与训练集排除
# ---------------------------------------------------------------------------


def test_export_masking_and_train_exclusion():
    sys.path.insert(0, str(Path("scripts").resolve()))
    import export_review_set as ers

    assert "138****8000" in ers._mask("我的手机号是13800138000帮我查下")
    train = ers._load_train_texts()
    assert isinstance(train, set) and len(train) > 1000  # v42 训练集已存在
