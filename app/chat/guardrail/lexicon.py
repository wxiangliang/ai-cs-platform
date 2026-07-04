"""护栏规则库加载（Stage 14）。

规则的单一事实来源是 `docs/chat/skills_design/guardrails.md` 的
【机器可读规则库】表格（与技能声明同机制：文档即配置）。
本模块解析表格行 `| rule_id | 类别 | 模式/词条 | 动作 |`：
- injection / output_leak：模式列是正则（表格里 `\\|` 转义竖线）；
- abuse_severe / emotion_negative：模式列是逗号分隔词条（子串匹配）。

加载失败（文件缺失/正则非法）只告警并跳过该条——护栏降级放行，
绝不因规则库问题打断主链路（fail-open，与限流同原则）。
"""

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# 锚定仓库根（Stage 13 口径）：任意 CWD 启动都能找到
_RULES_FILE = Path(__file__).resolve().parents[3] / "docs/chat/skills_design/guardrails.md"

# 正则类目 / 词表类目
_REGEX_CATEGORIES = frozenset({"injection", "output_leak"})
_LEXICON_CATEGORIES = frozenset({"abuse_severe", "emotion_negative"})

_ROW_RE = re.compile(
    r"^\|\s*([A-Z]{3}-\d{3})\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|\s*(block|flag)\s*\|$"
)


@dataclass(frozen=True)
class GuardrailRule:
    """一条护栏规则。"""

    rule_id: str
    category: str  # injection / abuse_severe / emotion_negative / output_leak
    action: str  # block / flag
    pattern: re.Pattern | None = None  # 正则类目
    terms: tuple[str, ...] = ()  # 词表类目

    def match(self, text: str) -> bool:
        """本条规则是否命中文本。"""
        if self.pattern is not None:
            return bool(self.pattern.search(text))
        return any(term in text for term in self.terms)


def load_rules(path: Path | None = None) -> list[GuardrailRule]:
    """解析规则库文档。文件缺失返回空表（护栏整体降级放行 + 告警）。"""
    file = path or _RULES_FILE
    if not file.exists():
        logger.warning("guardrail rules file not found: %s, guardrail degraded to pass", file)
        return []
    rules: list[GuardrailRule] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        rule_id, category, raw, action = m.groups()
        raw = raw.replace("\\|", "|")  # 表格转义还原
        try:
            if category in _REGEX_CATEGORIES:
                rules.append(
                    GuardrailRule(
                        rule_id=rule_id,
                        category=category,
                        action=action,
                        pattern=re.compile(raw, re.IGNORECASE),
                    )
                )
            elif category in _LEXICON_CATEGORIES:
                terms = tuple(t.strip() for t in raw.split(",") if t.strip())
                rules.append(
                    GuardrailRule(rule_id=rule_id, category=category, action=action, terms=terms)
                )
            else:
                logger.warning("guardrail rule %s: unknown category %s, skipped", rule_id, category)
        except re.error:
            # 单条正则非法只跳过该条，不影响其余规则（fail-open）
            logger.exception("guardrail rule %s: invalid regex, skipped", rule_id)
    logger.info("guardrail rules loaded: %d from %s", len(rules), file.name)
    return rules
