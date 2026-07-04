"""混合意图分类器（HybridIntentClassifier，Stage 04-02 + 04-01 LLM 二判）。

四层架构（taxonomy 第 8 节，判定顺序与优先级严格一致）：
1. 规则控制层：取消订单 / META 控制 / 确认门应答 / 纯槽位——确定性短路，不交给模型；
2. SetFit 语义层：29 类业务/闲聊/FAQ 意图，按风险分级阈值采纳；
3. LLM 难例二判（Stage 04-01）：SetFit 低置信样本交 LLM few-shot 二次判定
   （prompt 含意图目录与边界裁决规则），成本只花在难例上；无 Key/失败降级第 4 层；
4. 兜底：META.UNKNOWN（走澄清/知识库）；SetFit 模型不可用时整体退回规则分类器全表。

实现 app/chat/intent/base.py 的 IntentClassifier 协议。
"""

import asyncio

from app.chat.intent.catalog import INTENT_DESCRIPTIONS
from app.chat.intent.rule_classifier import rule_intent_classifier
from app.chat.intent.setfit_classifier import setfit_intent_model
from app.chat.intent.types import DecisionSource, IntentLabel, IntentResult
from app.chat.llm.factory import chat_completion, llm_available
from app.chat.llm.prompts import build_intent_second_opinion_prompt
from app.chat.state.types import DialogStateValue
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 写操作意图（taxonomy 风险 L2/L3）：错判代价高，采用更高的置信度阈值。
# 即便错判进入任务，确认门仍是最后防线，但宁可多一次澄清也不误开高风险流程。
_WRITE_INTENTS = frozenset(
    {
        IntentLabel.ORDER_CANCEL,
        "ORDER.CHANGE_ADDRESS",
        "ORDER.CREATE",
        IntentLabel.AFTERSALE_REFUND,
        IntentLabel.AFTERSALE_RETURN,
        IntentLabel.AFTERSALE_EXCHANGE,
        "AFTERSALE.REPAIR",
        "PAYMENT.INVOICE",
    }
)


class HybridIntentClassifier:
    """规则控制层 + SetFit 语义层 + 规则降级。"""

    async def classify(
        self,
        text: str,
        *,
        current_state: str = DialogStateValue.IDLE,
        has_active_task: bool = False,
    ) -> IntentResult:
        """对归一化文本做混合意图识别。"""
        normalized = (text or "").strip()
        if not normalized:
            return IntentResult(
                pred_label=IntentLabel.META_UNKNOWN,
                confidence=0.0,
                decision_source=DecisionSource.RULE_FALLBACK,
            )

        # —— 第 1 层：规则控制层（确定性短路）——
        control = rule_intent_classifier.classify_control(
            normalized, current_state=current_state, has_active_task=has_active_task
        )
        if control is not None:
            return control

        # —— 第 3 层前置检查：模型不可用 → 降级规则全表 ——
        if not await asyncio.to_thread(lambda: setfit_intent_model.available):
            result = await rule_intent_classifier.classify(
                normalized, current_state=current_state, has_active_task=has_active_task
            )
            result.decision_source = DecisionSource.SETFIT_FALLBACK_RULE
            return result

        # —— 第 2 层：SetFit 语义层（CPU 密集，线程池执行避免阻塞事件循环）——
        try:
            label, confidence, top_k = await asyncio.to_thread(
                setfit_intent_model.predict, normalized
            )
        except Exception:  # noqa: BLE001 - 推理异常降级规则，不打断主链路
            logger.exception("setfit predict failed, fallback to rule classifier")
            result = await rule_intent_classifier.classify(
                normalized, current_state=current_state, has_active_task=has_active_task
            )
            result.decision_source = DecisionSource.SETFIT_FALLBACK_RULE
            return result

        # 风险分级阈值：写操作意图要求更高置信度
        threshold = (
            settings.INTENT_CONFIDENCE_THRESHOLD_WRITE
            if label in _WRITE_INTENTS
            else settings.INTENT_CONFIDENCE_THRESHOLD
        )
        if confidence < threshold:
            # —— 第 3 层：LLM 难例二判（成本只花在低置信样本上）——
            second = await self._llm_second_opinion(normalized, top_k)
            if second is not None:
                return second
            # 兜底 UNKNOWN：走澄清/知识库路径；top_k 保留供排查与数据回流
            return IntentResult(
                pred_label=IntentLabel.META_UNKNOWN,
                confidence=confidence,
                decision_source=DecisionSource.SETFIT_LOW_CONF,
                top_k=top_k,
            )

        return IntentResult(
            pred_label=label,
            confidence=confidence,
            decision_source=DecisionSource.SETFIT,
            top_k=top_k,
        )

    @staticmethod
    async def _llm_second_opinion(text: str, top_k: list[dict]) -> IntentResult | None:
        """LLM 二判：输出必须是目录内的意图码，否则视为失败（返回 None 走兜底）。

        写操作意图的采纳不放宽：LLM 二判结果若是写意图，仍要求下游确认门把关
        （执行侧的防线，见 taxonomy 第 5 节风险等级）。
        """
        if not (settings.LLM_INTENT_SECOND_OPINION and llm_available()):
            return None
        system, user = build_intent_second_opinion_prompt(text, top_k)
        raw = await chat_completion(system, user, purpose="classify")
        if raw is None:
            return None
        label = raw.strip().strip("。.`\"'")
        if label not in INTENT_DESCRIPTIONS:
            logger.warning("llm second opinion returned unknown label: %r", label[:50])
            return None
        return IntentResult(
            pred_label=label,
            confidence=0.7,  # LLM 二判不产出概率，给固定中高置信标记
            decision_source=DecisionSource.LLM,
            top_k=top_k,
        )


# 模块级单例
hybrid_intent_classifier = HybridIntentClassifier()
