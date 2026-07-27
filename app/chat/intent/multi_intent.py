"""多意图检测（Stage 10）。

一句话带多个诉求（「我要退款，顺便查下这个订单的物流」）时：
- 按并列标记与句读把文本切成段（≤3 段有效段）；
- 每段独立分类 + 独立抽槽（防串槽：「退款订单A1，查B2物流」A1 归退款、B2 归物流）；
- ≥2 个不同业务意图 → 主意图 = 第一段（尊重用户表达顺序），
  次要意图构造 pending 任务（连同该段槽位）交状态机压入任务栈——
  主任务结束时自动恢复（复用 Stage 05 机制），不新造流转。

约束：控制层意图（转人工/放弃/确认门应答）整句命中时不拆分；
CHITCHAT/UNKNOWN 段在存在业务意图时忽略。
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from app.chat.intent.base import IntentClassifier
from app.chat.intent.rule_classifier import rule_intent_classifier
from app.chat.intent.types import IntentLabel, IntentResult
from app.chat.slots.extractor import slot_extractor
from app.core.logging import get_logger

logger = get_logger(__name__)

# 并列标记（v2 按复合句评估集扩充：但是/而且/也想/也要/再问 等）
_MARKERS = r"另外|顺便|还有|再帮我|帮我再|然后|以及|同时|对了|再查|再看下|再看看|再问|但是|而且|也想|也要|还想"
_MARKER_RE = re.compile(_MARKERS)
# 分段切分：并列标记 + 强句读
_SPLIT_RE = re.compile(rf"(?:{_MARKERS}|[，。；,;！!？?])")
# 无标记时的触发条件：含子句标点且切出 ≥2 个有效子句（「这个多少钱，有没有库存」
# 这类逗号复合句占真实多意图近半，v2 起也尝试拆分；误拆由同意图合并与
# 非业务段过滤兜底，代价是多几次段级分类，SetFit 段级推理 ~15ms 可接受）
_CLAUSE_PUNCT_RE = re.compile(r"[，。；,;！!？?]")

# 这些意图不作为多意图的组成部分（控制类整句优先；闲聊/兜底在有业务意图时忽略）
_NON_BUSINESS = {
    IntentLabel.META_ABORT,
    IntentLabel.META_TRANSFER_HUMAN,
    IntentLabel.META_BOT_IDENTITY,
    IntentLabel.META_CONFIRM,
    IntentLabel.META_DENY,
    IntentLabel.META_SLOT_ONLY,
    IntentLabel.META_UNKNOWN,
    IntentLabel.CHITCHAT_GENERAL,
    IntentLabel.CHITCHAT_THANKS,
}

_MAX_SEGMENTS = 3
_MAX_PENDING = 2


@dataclass
class MultiIntentResult:
    """多意图拆分结果。"""

    primary: IntentResult  # 主意图（第一段）
    primary_text: str  # 主意图对应的段文本（供槽位抽取，防串槽）
    pending: list[dict[str, Any]] = field(default_factory=list)  # [{intent, slots}]


async def detect_multi_intent(
    text: str,
    classifier: IntentClassifier,
    *,
    current_state: str,
    has_active_task: bool,
) -> MultiIntentResult | None:
    """尝试多意图拆分；不满足条件返回 None（走单意图路径）。"""
    # 触发条件：显式并列标记，或逗号/句读切出多个有效子句
    if not _MARKER_RE.search(text):
        clause_count = sum(
            1 for s in _CLAUSE_PUNCT_RE.split(text) if len(s.strip()) >= 4
        )
        if clause_count < 2:
            return None
    # META 控制意图整句命中 → 不拆分（「算了，都不要了」不能拆出业务段）。
    # 注意：控制层也会命中 ORDER.CANCEL（业务意图），它不应阻止拆分——
    # 「帮我取消订单，再问下退款多久到」仍需拆出第二个诉求
    control = rule_intent_classifier.classify_control(
        text, current_state=current_state, has_active_task=has_active_task
    )
    if control is not None and control.pred_label != IntentLabel.ORDER_CANCEL:
        return None

    segments = [s.strip() for s in _SPLIT_RE.split(text) if s.strip() and len(s.strip()) >= 2]
    if len(segments) < 2:
        return None
    segments = segments[:_MAX_SEGMENTS]

    # 每段独立分类（并行——各段互不依赖，SetFit 线程池推理/LLM 二判可并发，
    # 3 段耗时从三者之和降为三者最大值），忽略非业务段；相邻同意图段合并（槽位一起抽）
    results = await asyncio.gather(
        *(
            classifier.classify(seg, current_state=current_state, has_active_task=has_active_task)
            for seg in segments
        )
    )
    classified: list[tuple[str, str, IntentResult]] = []  # (intent, seg_text, result)
    for seg, result in zip(segments, results):
        if result.pred_label in _NON_BUSINESS:
            continue
        if classified and classified[-1][0] == result.pred_label:
            prev_intent, prev_text, prev_result = classified[-1]
            classified[-1] = (prev_intent, f"{prev_text} {seg}", prev_result)
        else:
            classified.append((result.pred_label, seg, result))

    distinct = {c[0] for c in classified}
    if len(distinct) < 2:
        return None

    primary_intent, primary_text, primary_result = classified[0]
    pending: list[dict[str, Any]] = []
    for intent, seg_text, _ in classified[1 : 1 + _MAX_PENDING]:
        pending.append({"intent": intent, "slots": slot_extractor.extract(seg_text)})

    logger.info(
        "multi-intent detected: primary=%s pending=%s",
        primary_intent,
        [p["intent"] for p in pending],
    )
    return MultiIntentResult(primary=primary_result, primary_text=primary_text, pending=pending)
