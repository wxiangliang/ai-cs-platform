"""Stage 18 实验解析：确定性分桶 + 变体参数注入。

- 分桶：`md5(exp_id:tenant:session) % 100` 落变体——**确定性**（同会话稳定同变体，
  不中途换体）、无状态、多进程一致（不用内置 hash，其带 PYTHONHASHSEED 随机化）；
- 注入：把命中变体的参数覆盖写入 contextvar，白名单消费点经 `effective()` 读取，
  从而「只改参数、不改代码路径」；本轮结束下一请求重新 set，天然隔离；
- 落库：`resolve_experiment().to_log()` 进 decision_log.experiment_json，供事后按变体切分；
- 降级：配置缺失/解析失败 → 空解析（control 默认参数），绝不打断主链路。
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.experiments.config import Experiment, Variant, load_experiments

logger = get_logger(__name__)

# 本轮生效的参数覆盖：图入口 set，白名单消费点经 effective() 读取；
# 每个请求独立 set（含空），跨请求不泄漏
_overrides_ctx: ContextVar[dict[str, Any]] = ContextVar("experiment_overrides", default={})


@dataclass
class ExperimentResolution:
    """一次实验解析结果：变体分配（供落库）+ 合并后的参数覆盖（供 effective）。"""

    assignments: list[dict[str, str]] = field(default_factory=list)
    overrides: dict[str, Any] = field(default_factory=dict)

    def to_log(self) -> dict[str, Any] | None:
        """落 decision_log.experiment_json：无命中返回 None（不占字段）。"""
        if not self.assignments:
            return None
        return {"assignments": self.assignments, "overrides": self.overrides}


def bucket_of(experiment_id: str, tenant_id: str, session_id: str) -> int:
    """确定性分桶 [0,100)：同 (实验,租户,会话) 恒定同桶，多进程一致。"""
    key = f"{experiment_id}:{tenant_id}:{session_id}"
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 100


def pick_variant(exp: Experiment, bucket: int) -> Variant:
    """按累计权重把 bucket 映射到变体（权重归一化到 100）。"""
    total = sum(v.weight for v in exp.variants) or 1.0
    acc = 0.0
    for v in exp.variants:
        acc += v.weight / total * 100.0
        if bucket < acc:
            return v
    # 浮点兜底：落最后一个变体
    return exp.variants[-1]


def resolve_experiment(tenant_id: str, session_id: str) -> ExperimentResolution:
    """解析当前 (租户,会话) 命中的运行中实验，返回变体分配与参数覆盖。

    多实验：各自独立分桶，参数覆盖合并（白名单已在配置层过滤；同名后者覆盖前者并告警）。
    任何异常 → 空解析（control），fail-open。
    """
    resolution = ExperimentResolution()
    try:
        for exp in load_experiments():
            if not exp.is_running() or not exp.in_tenant_scope(tenant_id):
                continue
            variant = pick_variant(exp, bucket_of(exp.id, tenant_id, session_id))
            resolution.assignments.append({"exp_id": exp.id, "variant": variant.name})
            for k, val in variant.params.items():
                if k in resolution.overrides and resolution.overrides[k] != val:
                    logger.warning("experiment override conflict on %s, later wins", k)
                resolution.overrides[k] = val
    except Exception:  # noqa: BLE001 - 实验解析失败回落 control，不打断主链路
        logger.warning("resolve_experiment failed, fallback to control", exc_info=True)
        return ExperimentResolution()
    return resolution


def set_overrides(overrides: dict[str, Any]) -> None:
    """写入本轮参数覆盖（图入口调用，每请求都 set，含空 dict 以清上一轮）。"""
    _overrides_ctx.set(dict(overrides or {}))


def effective(name: str, default: Any = None) -> Any:
    """读有效配置：本轮实验覆盖优先，否则回落 settings（白名单消费点统一走此）。"""
    overrides = _overrides_ctx.get()
    if name in overrides:
        return overrides[name]
    return getattr(settings, name, default)
