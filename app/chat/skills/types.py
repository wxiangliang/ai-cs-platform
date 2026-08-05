"""Skill 类型定义。

Skill 是“处理某类意图所需的能力声明”，不是业务代码本身。
Stage 05 起为「双源合并」：代码注册表提供运行时模板与必填槽位，
仓库根 skills/ 的 md 文件（YAML front-matter，schema 在 docs/chat/skills_design/）提供工具/动作/约束等能力声明，
由 loader 在启动时合并（见 app/chat/skills/loader.py）。
"""

from dataclasses import dataclass, field
from typing import Any


class SkillKind:
    """Skill 性质。

    READ：读操作，槽位齐全即可 DONE；
    WRITE：写操作（退款/退货/换货等），槽位齐全也只进确认门 CONFIRMING，绝不直接执行；
    META：控制 / 身份 / 投诉等，直接给模板回复；
    CHITCHAT：闲聊。
    """

    READ = "read"
    WRITE = "write"
    META = "meta"
    CHITCHAT = "chitchat"


@dataclass
class ToolSpec:
    """Skill 声明的工具依赖（md front-matter 的 required_tools 项）。"""

    tool_id: str
    purpose: str = ""
    required_slots: list[str] = field(default_factory=list)
    optional: bool = False


@dataclass
class ActionSpec:
    """Skill 声明的写操作（md front-matter 的 actions 项）。

    requires_confirmation=True 的动作只能由 ActionExecutor 在确认门通过后执行。
    """

    action_id: str
    description: str = ""
    requires_confirmation: bool = True
    confirmation_prompt: str = ""
    rollback: bool = False


@dataclass
class Skill:
    """能力声明。

    templates 约定的 key：
    - collect：缺槽位时的追问话术；
    - confirm：写操作的确认门话术（可用 {order_id} 等占位）；
    - confirmed：确认门通过后的受理/执行回执；
    - answer：读操作完成 / META / 闲聊的回复。
    """

    skill_id: str
    name: str
    domain: str
    intent: str
    kind: str
    required_slots: list[str] = field(default_factory=list)
    templates: dict[str, str] = field(default_factory=dict)
    # —— Stage 05：来自 skills_design md 文件的能力声明 ——
    risk_level: str = "L0"  # L0-L3，见 taxonomy 第 5 节
    priority: int = 70
    required_tools: list[ToolSpec] = field(default_factory=list)
    actions: list[ActionSpec] = field(default_factory=list)
    tool_returns: list[str] = field(default_factory=list)  # 工具返回字段（占位符来源）
    constraints: dict[str, Any] = field(default_factory=dict)
    rag_fallback: bool = False  # 工具无结果时转知识库检索（检索路由 R4）
    prompt_fragment: str = ""  # md body，注入 LLM 的场景话术说明
