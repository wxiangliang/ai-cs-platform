"""意图分类器工厂：按 INTENT_CLASSIFIER 配置返回实现。

图节点只依赖本工厂与 IntentClassifier 协议，不 import 具体实现，
便于按配置切换与后续扩展（LLM 难例二判等）。
"""

from functools import lru_cache

from app.chat.intent.base import IntentClassifier
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_intent_classifier() -> IntentClassifier:
    """返回当前配置的意图分类器（进程内单例）。

    rule   ：纯规则（Stage 03 行为）；
    hybrid ：规则控制层 + SetFit 语义层（Stage 04-02，推荐）。
    未知配置回落 rule 并告警。
    """
    mode = settings.INTENT_CLASSIFIER.lower()
    if mode == "hybrid":
        from app.chat.intent.hybrid_classifier import hybrid_intent_classifier

        return hybrid_intent_classifier
    if mode != "rule":
        logger.warning("unknown INTENT_CLASSIFIER=%s, fallback to rule", settings.INTENT_CLASSIFIER)
    from app.chat.intent.rule_classifier import rule_intent_classifier

    return rule_intent_classifier
