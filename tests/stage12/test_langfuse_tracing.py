"""Langfuse 链路追踪单元测试（Stage 12）。"""

from app.core import tracing
from app.core.config import settings


def _reset_cache():
    tracing._init_client.cache_clear()


def test_disabled_without_keys(monkeypatch):
    """默认无 Key：不启用，handler 为 None（主链路零感知）。"""
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "")
    _reset_cache()
    assert tracing.langfuse_enabled() is False
    assert tracing.get_langfuse_handler() is None
    tracing.shutdown_langfuse()  # 空操作不抛异常


def test_disabled_by_switch(monkeypatch):
    """有 Key 但开关关闭：不启用。"""
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-lf-x")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-lf-x")
    _reset_cache()
    assert tracing.get_langfuse_handler() is None


def test_handler_created_with_keys(monkeypatch):
    """配置双 Key：返回 CallbackHandler（初始化不联网，上报后台异步）。"""
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setattr(settings, "LANGFUSE_HOST", "http://localhost:13000")
    _reset_cache()
    handler = tracing.get_langfuse_handler()
    assert handler is not None
    assert "CallbackHandler" in type(handler).__name__
    _reset_cache()  # 清理，避免全局 client 泄漏到其他用例


def test_trace_metadata_fields():
    meta = tracing.build_trace_metadata(
        session_id="s1", user_id="u1", tenant_id="t1", channel="web", trace_id="trace_x"
    )
    assert meta["langfuse_session_id"] == "s1"
    assert meta["langfuse_user_id"] == "u1"
    assert "tenant:t1" in meta["langfuse_tags"]
    assert meta["trace_id"] == "trace_x"
