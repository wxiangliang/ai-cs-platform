"""Skill Loader（Stage 05）。

从仓库根 skills/*.md 解析 YAML front-matter（schema 见 docs/chat/skills_design/00_skill_schema.md），
把能力声明（required_tools / actions / tool_returns / constraints / risk_level /
priority / rag_fallback / prompt_fragment）合并进代码注册表的 Skill 对象。

双源合并的分工（决策记录见 stage-05 需求附录）：
- 代码注册表（registry.py）：运行时模板（collect/confirm/confirmed/answer）与
  运行时必填槽位（与 SlotExtractor 能力对齐，如 order_id）；
- md 文件：工具/动作/约束等能力声明（业务视角槽位如 customer_phone_or_order_id
  留在 md，运行时映射见 taxonomy 第 7 节槽位字典）。

启动校验（SKILL_LOADER_STRICT 控制告警级别）：
- 硬错误（直接抛异常拒绝启动）：skill_id 不在意图目录、domain 不在 10 域枚举、
  缺 risk_level/priority、YAML 解析失败；
- 告警（strict=true 时升级为硬错误）：confirmation_prompt 占位符在
  slots ∪ tool_returns ∪ 系统上下文白名单中找不到来源。
"""

import re
from pathlib import Path
from typing import Any

import yaml

from app.chat.intent.catalog import INTENT_DESCRIPTIONS
from app.chat.skills.types import ActionSpec, Skill, ToolSpec
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 锚定仓库根（Stage 13）：从任意 CWD 启动都能找到，不再依赖工作目录
_REPO_ROOT = Path(__file__).resolve().parents[3]
# Stage 31 起技能声明移至仓库根 skills/（运行时加载物不放 docs；
# schema 规范与护栏规则库仍在 docs/chat/skills_design/）
SKILLS_DIR = _REPO_ROOT / "skills"

_DOMAINS = {
    "PRODUCT", "ORDER", "LOGISTICS", "AFTERSALE", "PAYMENT",
    "PROMOTION", "FAQ", "META", "CHITCHAT",
    "MEMBER",  # Stage 33 第 10 域（会员注册引导）
}
# 上下文敏感 META 意图：在 taxonomy 注册但不在 SetFit/二判目录，loader 单独放行
_CONTEXT_META = {"META.SLOT_ONLY", "META.CORRECTION", "META.DENY"}
# 占位符的系统上下文白名单
_SYSTEM_PLACEHOLDERS = {"user_id", "tenant_id"}
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


class SkillLoadError(Exception):
    """Skill 声明校验失败（硬错误，拒绝启动）。"""


def _parse_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    """拆 YAML front-matter 与 Markdown body。"""
    text = path.read_text(encoding="utf-8")
    parts = text.split("---")
    if len(parts) < 3:
        raise SkillLoadError(f"{path.name}: 缺少 YAML front-matter")
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"{path.name}: YAML 解析失败 {exc}") from exc
    body = "---".join(parts[2:]).strip()
    return meta, body


def _validate(path: Path, meta: dict[str, Any], warnings: list[str]) -> None:
    """启动校验：硬错误抛异常，占位符来源问题记入 warnings。"""
    skill_id = meta.get("skill_id", "")
    if skill_id not in INTENT_DESCRIPTIONS and skill_id not in _CONTEXT_META:
        raise SkillLoadError(f"{path.name}: skill_id={skill_id} 不在意图目录（catalog.py/taxonomy）")
    if meta.get("domain") not in _DOMAINS:
        raise SkillLoadError(f"{path.name}: domain={meta.get('domain')} 不在 10 域枚举")
    if not meta.get("risk_level") or meta.get("priority") is None:
        raise SkillLoadError(f"{path.name}: 缺少 risk_level / priority（schema v2 必填）")

    # 占位符来源校验：slots ∪ tool_returns ∪ 系统白名单
    declared = {s.get("name") for s in meta.get("slots") or [] if isinstance(s, dict)}
    declared |= {t.get("name") for t in meta.get("tool_returns") or [] if isinstance(t, dict)}
    declared |= _SYSTEM_PLACEHOLDERS
    for action in meta.get("actions") or []:
        prompt = (action or {}).get("confirmation_prompt") or ""
        unknown = set(_PLACEHOLDER_RE.findall(prompt)) - declared
        if unknown:
            warnings.append(f"{path.name}: confirmation_prompt 占位符来源未声明 {sorted(unknown)}")


def _to_specs(meta: dict[str, Any]) -> tuple[list[ToolSpec], list[ActionSpec], list[str]]:
    """front-matter → ToolSpec/ActionSpec/tool_returns。"""
    tools = [
        ToolSpec(
            tool_id=t.get("tool_id", ""),
            purpose=t.get("purpose", ""),
            required_slots=list(t.get("required_slots") or []),
            optional=bool(t.get("optional", False)),
        )
        for t in meta.get("required_tools") or []
        if isinstance(t, dict) and t.get("tool_id")
    ]
    actions = [
        ActionSpec(
            action_id=a.get("action_id", ""),
            description=a.get("description", ""),
            requires_confirmation=bool(a.get("requires_confirmation", True)),
            confirmation_prompt=a.get("confirmation_prompt", "") or "",
            rollback=bool(a.get("rollback", False)),
        )
        for a in meta.get("actions") or []
        if isinstance(a, dict) and a.get("action_id")
    ]
    returns = [
        t.get("name", "")
        for t in meta.get("tool_returns") or []
        if isinstance(t, dict) and t.get("name")
    ]
    return tools, actions, returns


def load_skill_declarations() -> dict[str, dict[str, Any]]:
    """加载并校验全部 Skill md 声明，返回 {skill_id: 声明字段}。

    校验失败抛 SkillLoadError（禁止带病上线）；
    占位符告警按 SKILL_LOADER_STRICT 决定是否升级为硬错误。
    """
    if not SKILLS_DIR.exists():
        raise SkillLoadError(f"skills 目录不存在: {SKILLS_DIR}")

    declarations: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue  # 目录说明文档，不是技能声明
        meta, body = _parse_front_matter(path)
        _validate(path, meta, warnings)
        tools, actions, returns = _to_specs(meta)
        declarations[meta["skill_id"]] = {
            "risk_level": str(meta["risk_level"]),
            "priority": int(meta["priority"]),
            "required_tools": tools,
            "actions": actions,
            "tool_returns": returns,
            "constraints": meta.get("constraints") or {},
            "rag_fallback": bool(meta.get("rag_fallback", False)),
            "prompt_fragment": body,
        }

    if warnings:
        message = "skill 声明占位符告警（%d 条）：%s" % (len(warnings), "; ".join(warnings))
        if settings.SKILL_LOADER_STRICT:
            raise SkillLoadError(message)
        logger.warning(message)

    logger.info("skill declarations loaded: %d files", len(declarations))
    return declarations


def apply_declarations(skills: dict[str, Skill]) -> None:
    """把 md 声明合并进代码注册表的 Skill 对象（按 intent=skill_id 对应）。

    代码注册表里没有对应 md 的技能保持默认；md 有而代码没有的技能只记日志
    （运行时可达意图以注册表为准，守护测试见 tests/intent/test_registry_coverage.py）。
    """
    declarations = load_skill_declarations()
    matched = 0
    for intent, skill in skills.items():
        decl = declarations.get(intent)
        if decl is None:
            continue
        skill.risk_level = decl["risk_level"]
        skill.priority = decl["priority"]
        skill.required_tools = decl["required_tools"]
        skill.actions = decl["actions"]
        skill.tool_returns = decl["tool_returns"]
        skill.constraints = decl["constraints"]
        skill.rag_fallback = decl["rag_fallback"]
        skill.prompt_fragment = decl["prompt_fragment"]
        matched += 1
    unmatched = set(declarations) - set(skills)
    if unmatched:
        logger.info("skill md 声明暂无运行时注册（正常，逐步接入）：%s", sorted(unmatched))
    logger.info("skill declarations applied: %d merged", matched)
