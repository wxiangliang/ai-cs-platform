"""Stage 18 实验配置加载。

实验用 JSON 配置文件描述（路径 `settings.EXPERIMENTS_CONFIG_PATH`，留空=无实验）：

    {
      "experiments": [
        {
          "id": "rag_min_score_test",
          "status": "running",            // draft / running / stopped
          "scope": {"tenants": [], "intents": []},   // 空=全部
          "variants": [
            {"name": "control", "weight": 50, "params": {}},
            {"name": "low_thresh", "weight": 50, "params": {"RAG_MIN_SCORE": 0.4}}
          ]
        }
      ]
    }

红线：
- 变体 `params` 只能覆盖 `OVERRIDABLE_PARAMS` 白名单内的**已存在配置项**，
  不引入新代码分支——实验只能调参数，不能改逻辑；
- 配置缺失/损坏一律 fail-open（返回空列表），主链路走默认参数。

加载按文件 mtime 缓存，避免每轮读盘；改文件即自动重载。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 变体可覆盖的配置项白名单（settings 属性名）——只放「已存在、改了不引新分支」的可配项。
# 扩展前须确认该项的消费点通过 resolver.effective() 读取，否则覆盖不生效
OVERRIDABLE_PARAMS: frozenset[str] = frozenset(
    {
        "RAG_MIN_SCORE",
        "FAQ_HIT_THRESHOLD",
        "RAG_RECALL_TOP_K",
        "RERANKER_PROVIDER",
    }
)

_RUNNING = "running"


@dataclass(frozen=True)
class Variant:
    """一个实验变体：名称 + 分流权重 + 参数覆盖（已过白名单）。"""

    name: str
    weight: float
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Experiment:
    """一个实验：id / 状态 / 作用域（租户，空=全部）/ 变体列表。"""

    id: str
    status: str
    variants: list[Variant]
    scope_tenants: tuple[str, ...] = ()
    scope_intents: tuple[str, ...] = ()

    def is_running(self) -> bool:
        return self.status == _RUNNING and len(self.variants) > 0

    def in_tenant_scope(self, tenant_id: str) -> bool:
        """租户作用域判定：未配置 tenants 视为全部租户。"""
        return not self.scope_tenants or tenant_id in self.scope_tenants


# 按文件 mtime 缓存：{path: (mtime, experiments)}
_cache: dict[str, tuple[float, list[Experiment]]] = {}


def _parse_variant(raw: dict[str, Any]) -> Variant | None:
    """解析单个变体，非法（无名/权重非数）返回 None 由上层丢弃。"""
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        return None
    try:
        weight = float(raw.get("weight", 0))
    except (TypeError, ValueError):
        return None
    if weight < 0:
        return None
    raw_params = raw.get("params") or {}
    # 白名单过滤：非白名单参数丢弃并告警，杜绝实验偷改非可配项
    params: dict[str, Any] = {}
    if isinstance(raw_params, dict):
        for k, v in raw_params.items():
            if k in OVERRIDABLE_PARAMS:
                params[k] = v
            else:
                logger.warning("experiment param %s not in whitelist, ignored", k)
    return Variant(name=name, weight=weight, params=params)


def _parse_experiment(raw: dict[str, Any]) -> Experiment | None:
    """解析单个实验；缺 id / 无合法变体 → None。"""
    exp_id = raw.get("id")
    if not isinstance(exp_id, str) or not exp_id:
        return None
    variants = [v for v in (_parse_variant(x) for x in raw.get("variants") or []) if v]
    if not variants:
        return None
    scope = raw.get("scope") or {}
    tenants = tuple(str(t) for t in (scope.get("tenants") or []))
    intents = tuple(str(i) for i in (scope.get("intents") or []))
    return Experiment(
        id=exp_id,
        status=str(raw.get("status", "draft")),
        variants=variants,
        scope_tenants=tenants,
        scope_intents=intents,
    )


def load_experiments() -> list[Experiment]:
    """加载实验配置（按 mtime 缓存）；未配置/文件缺失/损坏一律返回 []（fail-open）。"""
    path = settings.EXPERIMENTS_CONFIG_PATH
    if not path:
        return []
    try:
        p = Path(path)
        if not p.is_file():
            return []
        mtime = p.stat().st_mtime
        cached = _cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        raw = json.loads(p.read_text(encoding="utf-8"))
        items = raw.get("experiments") if isinstance(raw, dict) else None
        exps = [e for e in (_parse_experiment(x) for x in items or []) if e]
        _cache[path] = (mtime, exps)
        return exps
    except Exception:  # noqa: BLE001 - 配置损坏 fail-open，不打断主链路
        logger.warning("load experiments failed, fallback to none", exc_info=True)
        return []


def clear_cache() -> None:
    """清空配置缓存（测试用）。"""
    _cache.clear()
