"""能力规格加载与落点锚点校验（skills/capabilities/，活契约化）。

capabilities/ 里的规格不是被动文档：每个文件用 front-matter 声明
**落点锚点**（implemented_by：modules/intents/configs/events/metrics +
可选 playbook.yaml），本模块在启动时校验锚点真实存在——规格漂移
（模块被删/意图改名/配置移位）立刻暴露而不是烂在文档里
（Stage 25「告警引用指标静态交叉校验」同款模式）。

校验语义（与 Skill Loader 一致）：
- 硬错误：front-matter 缺失/损坏、capability_id 与文件名不符、status 非法；
- 锚点漂移：告警（SKILL_LOADER_STRICT=true 升级硬错误）；CI 由
  tests/skills/test_capability_specs.py 硬断言。
"""

from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES_DIR = _REPO_ROOT / "skills" / "capabilities"

_VALID_STATUS = {"implemented", "partial", "deferred"}


class CapabilitySpecError(Exception):
    """能力规格声明损坏（硬错误）。"""


def load_capability_specs() -> dict[str, dict[str, Any]]:
    """解析全部能力规格 front-matter；损坏抛 CapabilitySpecError。"""
    specs: dict[str, dict[str, Any]] = {}
    if not CAPABILITIES_DIR.exists():
        return specs
    for path in sorted(CAPABILITIES_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        parts = path.read_text(encoding="utf-8").split("---")
        if len(parts) < 3:
            raise CapabilitySpecError(f"{path.name}: 缺少 front-matter（落点锚点声明）")
        try:
            meta = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError as exc:
            raise CapabilitySpecError(f"{path.name}: YAML 解析失败 {exc}") from exc
        cap_id = str(meta.get("capability_id") or "")
        if cap_id != path.stem:
            raise CapabilitySpecError(
                f"{path.name}: capability_id={cap_id!r} 与文件名不符"
            )
        status = str(meta.get("status") or "").split()[0] if meta.get("status") else ""
        if status not in _VALID_STATUS:
            raise CapabilitySpecError(f"{path.name}: status={status!r} 非法（{_VALID_STATUS}）")
        specs[cap_id] = {
            "status": status,
            "implemented_by": meta.get("implemented_by") or {},
            "path": path,
        }
    return specs


def _metric_names() -> set[str]:
    """metrics 模块里全部指标名（Counter 注册名不含 _total 后缀，两种都收）。"""
    from prometheus_client import Counter, Histogram

    from app.core import metrics as metrics_mod

    names: set[str] = set()
    for value in vars(metrics_mod).values():
        if isinstance(value, (Counter, Histogram)):
            base = value._name  # noqa: SLF001 - prometheus_client 无公开访问器
            names.add(base)
            names.add(f"{base}_total")
    return names


def validate_capability_anchors() -> list[str]:
    """校验全部规格的落点锚点；返回漂移问题列表（空=全部成立）。"""
    from app.chat.intent.catalog import INTENT_DESCRIPTIONS

    issues: list[str] = []
    metric_names = _metric_names()
    for cap_id, spec in load_capability_specs().items():
        if spec["status"] == "deferred":
            continue  # 未实现的规格不校验锚点
        anchors: dict[str, Any] = spec["implemented_by"]
        if not any(anchors.get(k) for k in ("modules", "intents", "configs", "events", "metrics")):
            issues.append(f"{cap_id}: status={spec['status']} 但未声明任何落点锚点")
            continue
        for module in anchors.get("modules") or []:
            if not (_REPO_ROOT / str(module)).exists():
                issues.append(f"{cap_id}: 模块锚点不存在 {module}")
        for intent in anchors.get("intents") or []:
            if intent not in INTENT_DESCRIPTIONS:
                issues.append(f"{cap_id}: 意图锚点不在目录 {intent}")
        for config in anchors.get("configs") or []:
            if not (_REPO_ROOT / str(config)).exists():
                issues.append(f"{cap_id}: 配置锚点不存在 {config}")
        events = anchors.get("events") or []
        if events:
            from app.services.event_service import EVENT_RULES

            for event in events:
                if event not in EVENT_RULES:
                    issues.append(f"{cap_id}: 事件锚点不在白名单 {event}")
        for metric in anchors.get("metrics") or []:
            if str(metric) not in metric_names:
                issues.append(f"{cap_id}: 指标锚点不存在 {metric}")
        # playbook 参考件（若有）：入口意图必须在目录（防再次出现
        # PRODUCT.SELECTION_HELP 这类规格与实现脱节的意图名）
        playbook = spec["path"].with_name(f"{cap_id}.playbook.yaml")
        if playbook.exists():
            try:
                pb = yaml.safe_load(playbook.read_text(encoding="utf-8")) or {}
                for intent in pb.get("entry_intents") or []:
                    if intent not in INTENT_DESCRIPTIONS:
                        issues.append(f"{cap_id}: playbook 入口意图不在目录 {intent}")
            except yaml.YAMLError:
                issues.append(f"{cap_id}: playbook.yaml 解析失败")
    return issues
