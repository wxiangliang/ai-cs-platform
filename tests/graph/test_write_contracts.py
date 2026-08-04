"""节点写契约测试（post-stage-27 工程护栏 ①）。

三层锁定：
1. 契约表完整性：builder 注册的节点 == 契约表键集合（新增节点忘登记 → 红）；
2. 契约字段真实性：每个字段必须是 GraphState 声明过的键（防拼写/幽灵字段）；
3. 执法行为：越权写在非 prod 抛错、prod 只告警放行、合规写原样透传。
"""

import pytest

from app.chat.graph import builder
from app.chat.graph.contracts import (
    GraphContractViolation,
    NODE_WRITES,
    enforce_write_contract,
)
from app.chat.graph.state import GraphState
from app.core.config import settings


def _registered_node_names() -> set[str]:
    names = {name for name, _ in builder._LINEAR_SEQUENCE}
    names |= set(builder._REPLY_NODES)
    names.add("save_turn")
    return names


def test_every_registered_node_has_contract():
    assert _registered_node_names() == set(NODE_WRITES)


def test_contract_fields_exist_in_graph_state():
    declared = set(GraphState.__annotations__)
    for node, fields in NODE_WRITES.items():
        ghost = fields - declared
        assert not ghost, f"{node} 契约含 GraphState 未声明字段: {ghost}"


def test_graph_builds_with_contract_wrappers():
    """图能正常构建编译（包装器不破坏 LangGraph 的签名探测）。"""
    assert builder.build_chat_graph() is not None


# ---------------- 执法行为 ----------------


async def _rogue_node(state):
    return {"reply": "ok", "active_task": {"intent": "X"}, "graph_trace": ["rogue"]}


async def _lawful_node(state):
    return {"reply": "ok", "graph_trace": ["lawful"]}


async def test_illegal_write_raises_in_dev():
    wrapped = enforce_write_contract("response_generate", _rogue_node)
    with pytest.raises(GraphContractViolation, match="active_task"):
        await wrapped({})


async def test_illegal_write_only_warns_in_prod(monkeypatch):
    """生产类环境不为契约 bug 打断用户会话。"""
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    wrapped = enforce_write_contract("response_generate", _rogue_node)
    result = await wrapped({})
    assert result["reply"] == "ok"  # 放行


async def test_lawful_write_passes_through():
    wrapped = enforce_write_contract("response_generate", _lawful_node)
    result = await wrapped({})
    assert result == {"reply": "ok", "graph_trace": ["lawful"]}


async def test_graph_trace_always_allowed():
    """graph_trace 是 add reducer，人人可追加，不算越权。"""

    async def trace_only(state):
        return {"graph_trace": ["x"]}

    wrapped = enforce_write_contract("slot_extract", trace_only)
    assert await wrapped({}) == {"graph_trace": ["x"]}


def test_wrapper_preserves_signature():
    """functools.wraps 保留原签名——LangGraph 依赖签名决定是否传 config。"""
    import inspect

    from app.chat.graph.nodes.save_turn import save_turn

    wrapped = enforce_write_contract("save_turn", save_turn)
    assert "config" in inspect.signature(wrapped).parameters