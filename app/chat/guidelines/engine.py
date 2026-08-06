"""行为准则引擎（Stage 40）：加载 / 规则匹配 / 渲染注入块 / 命中留痕。

纪律（需求第 2/3 节）：
- **准则层管「应该怎样」，护栏管「绝不允许」**——本层不拦截、不含事实数据；
- v1 全规则匹配（零 LLM 成本零延迟）：condition 各维度 AND、维度内 OR、
  空维度不限；criticality 排序 + 同 exclusion_group 去重 + 注入条数封顶
  （防重蹈系统提示词膨胀）；
- 只注入 LLM 增强路径（润色/RAG 生成/澄清）——确定性模板路径不经过这里；
- 命中 id 经 turn 级 contextvar 收集器由 save_turn 落
  graph_trace_json.guidelines（零 GraphState/节点契约改动）。
配置损坏/缺失一律 fail-open 空表。
"""

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Mapping

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import count_guideline

logger = get_logger(__name__)

_CRITICALITY_RANK = {"HIGH": 2, "NORMAL": 1, "LOW": 0}

# mtime 缓存：{path: (mtime, guidelines)}
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# turn 级命中收集器（chat_service 入口 reset，save_turn drain 落决策日志）
_matched: ContextVar[list[str] | None] = ContextVar("matched_guidelines", default=None)


def reset_matched_guidelines() -> None:
    """请求入口清空本轮命中收集器。"""
    _matched.set([])


def drain_matched_guidelines() -> list[str]:
    """取本轮命中的准则 id（去重保序）；save_turn 调用。"""
    ids = _matched.get() or []
    seen: list[str] = []
    for gid in ids:
        if gid not in seen:
            seen.append(gid)
    return seen


def _record(ids: list[str]) -> None:
    current = _matched.get()
    if current is None:
        _matched.set(list(ids))
    else:
        current.extend(ids)


def load_guidelines() -> list[dict[str, Any]]:
    """加载准则表（mtime 缓存；缺失/损坏返回 []）。"""
    path = settings.GUIDELINES_CONFIG_PATH
    if not path:
        return []
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[3] / p
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        guidelines = [
            g for g in data
            if isinstance(g, dict) and g.get("id") and g.get("action")
        ]
    except Exception:  # noqa: BLE001 - 配置损坏=空表，不打断主链路
        logger.warning("guidelines config invalid: %s", p, exc_info=True)
        guidelines = []
    _cache[path] = (mtime, guidelines)
    return guidelines


def _dimension_match(values: list[str] | None, actual: str) -> bool:
    """单维度匹配：空=不限；支持完整值或 `PREFIX.` 前缀（意图域）。"""
    if not values:
        return True
    return any(
        actual == v or (v.endswith(".") and actual.startswith(v))
        for v in values
        if isinstance(v, str)
    )


def match_guidelines(
    *,
    tenant_id: str,
    intent: str,
    state: str,
    emotion: str = "",
    text: str = "",
) -> list[dict[str, Any]]:
    """规则匹配（纯函数式，测试锁定）：AND 维度 × OR 取值 → 排序去重封顶。"""
    hits: list[dict[str, Any]] = []
    for g in load_guidelines():
        cond = g.get("condition") or {}
        if not _dimension_match(cond.get("tenants"), tenant_id):
            continue
        if not _dimension_match(cond.get("intents"), intent):
            continue
        if not _dimension_match(cond.get("states"), state):
            continue
        want_emotion = cond.get("emotion")
        if want_emotion and want_emotion != emotion:
            continue
        keywords = cond.get("keywords") or []
        if keywords and not any(k in text for k in keywords if isinstance(k, str)):
            continue
        hits.append(g)

    # criticality 降序（平级保持声明序）→ 同 exclusion_group 只留最高一条
    hits.sort(key=lambda g: -_CRITICALITY_RANK.get(str(g.get("criticality", "NORMAL")), 1))
    deduped: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    for g in hits:
        group = g.get("exclusion_group")
        if group:
            if group in seen_groups:
                continue
            seen_groups.add(group)
        deduped.append(g)
    return deduped[: settings.GUIDELINES_MAX_INJECT]


def guidelines_for_state(state: Mapping[str, Any]) -> str | None:
    """图节点入口：从 GraphState 取上下文 → 命中准则渲染注入块。

    返回 None = 未启用/空表/无命中（调用点零改动）；命中同时记入收集器
    （供 save_turn 留痕）与指标。
    """
    if not settings.GUIDELINES_ENABLED:
        return None
    try:
        intent_dict = state.get("intent_result") or {}
        intent = str(intent_dict.get("final_intent") or intent_dict.get("pred_label") or "")
        hits = match_guidelines(
            tenant_id=str(state.get("tenant_id") or ""),
            intent=intent,
            state=str(state.get("current_state") or ""),
            emotion=str(state.get("emotion") or ""),
            text=str(state.get("normalized_text") or ""),
        )
    except Exception:  # noqa: BLE001 - 准则层失败绝不影响生成
        logger.warning("guidelines match failed", exc_info=True)
        return None
    if not hits:
        return None
    _record([str(g["id"]) for g in hits])
    for g in hits:
        count_guideline(str(g.get("criticality", "NORMAL")))
    lines = "\n".join(f"- {g['action']}" for g in hits)
    return f"本轮行为准则（必须遵守，与安全红线冲突时以红线为准）：\n{lines}"
