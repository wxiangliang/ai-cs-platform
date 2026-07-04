"""规则意图识别器（RuleIntentClassifier）。

第一版基于关键词与正则的轻量识别，不调用 LLM、不依赖向量库。
实现 app/chat/intent/base.py 的 IntentClassifier 协议（async）。

识别优先级（与 docs/chat/intent_taxonomy.md 第 4 节一致，高 → 低）：
1. 取消订单（ORDER.CANCEL，带业务宾语，必须先于裸「取消」判定）；
2. META 控制类（转人工 / 取消 / 询问身份）；
3. 确认门应答（META.CONFIRM / META.DENY，仅 CONFIRMING 状态判定）；
4. 纯槽位输入（只发了订单号 / 手机号，必须含数字）；
5. 售后高风险类（退款 / 退货 / 换货 / 投诉）；
6. 订单 / 物流；商品；闲聊；
7. 兜底 META.UNKNOWN。
"""

import re

from app.chat.intent.types import DecisionSource, IntentLabel, IntentResult
from app.chat.slots.patterns import ORDER_ID_ONLY_RE, PHONE_ONLY_RE
from app.chat.state.types import DialogStateValue

# 「取消订单」类表达：宾语是订单 → 业务意图 ORDER.CANCEL，
# 必须先于 META.ABORT 的裸「取消」关键词判定（taxonomy 第 4 节裁决规则 1）
_CANCEL_ORDER_RE = re.compile(r"(取消.{0,4}(订单|这单|那单|单子))|((订单|这单|那单).{0,4}取消)|(退订)")

# META 控制类关键词（控制层专用，最高优先级；错判代价高、关键词精度足够，不交给语义模型）
_META_CONTROL_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (IntentLabel.META_TRANSFER_HUMAN, ("转人工", "人工客服", "找客服", "真人", "客服电话")),
    (IntentLabel.META_ABORT, ("算了", "不用了", "不需要", "取消", "没事了", "结束")),
    (IntentLabel.META_BOT_IDENTITY, ("你是机器人", "你是真人", "你是谁", "是不是机器人", "人工还是机器")),
]

# 业务意图关键词表（纯规则模式使用；混合模式下这些意图交给 SetFit 语义层）。
# 命中即返回，顺序即优先级。
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # —— 售后高风险类（写操作，优先于普通查询）——
    (IntentLabel.AFTERSALE_REFUND, ("退款", "退钱", "申请退")),
    (IntentLabel.AFTERSALE_RETURN, ("退货", "退回", "退掉")),
    (IntentLabel.AFTERSALE_EXCHANGE, ("换货", "换一个", "换货物", "调换")),
    (IntentLabel.AFTERSALE_COMPLAIN, ("投诉", "差评", "举报", "太差")),
    # —— 订单 / 物流 ——
    (IntentLabel.LOGISTICS_TRACK, ("物流", "快递", "到哪了", "发货了吗", "什么时候到", "运单")),
    (IntentLabel.ORDER_QUERY_STATUS, ("订单状态", "我的订单", "订单查询", "订单", "下单")),
    # —— 商品 ——
    (IntentLabel.PRODUCT_ASK_PRICE, ("多少钱", "价格", "报价", "贵不贵", "几块")),
    (IntentLabel.PRODUCT_ASK_STOCK, ("库存", "有货", "有没有货", "还有吗", "缺货")),
    (IntentLabel.PRODUCT_ASK_INFO, ("介绍", "参数", "规格", "怎么样", "配置", "详情")),
    # —— FAQ 知识问答（平台政策/规则类；更丰富的识别在 Stage 04 由 LLM 分类器承担）——
    (IntentLabel.FAQ_GENERAL, ("政策", "规定", "规则", "保修", "会员", "积分")),
    # —— 闲聊 ——
    (IntentLabel.CHITCHAT_THANKS, ("谢谢", "感谢", "多谢", "thanks", "thank you")),
    (IntentLabel.CHITCHAT_GENERAL, ("你好", "在吗", "hello", "hi", "您好")),
]

# 确认门应答词表（仅 CONFIRMING 状态判定，避免「好的」在闲聊场景被误判）
_CONFIRM_WORDS = ("确认", "确定", "是的", "对的", "没问题", "可以", "好的", "嗯", "对", "ok", "yes")
_DENY_WORDS = ("不对", "不要", "不用", "不确认", "先不", "别提交", "等等", "暂时不", "不了")

# 关键词命中的默认置信度
_KEYWORD_CONFIDENCE = 0.9
# 纯槽位输入的置信度
_SLOT_ONLY_CONFIDENCE = 0.8
# 确认门应答的置信度
_CONFIRM_GATE_CONFIDENCE = 0.9


class RuleIntentClassifier:
    """关键词规则意图识别器（实现 IntentClassifier 协议）。"""

    async def classify(
        self,
        text: str,
        *,
        current_state: str = DialogStateValue.IDLE,
        has_active_task: bool = False,
    ) -> IntentResult:
        """对预处理后的文本做意图识别。

        :param text: 归一化后的用户文本
        :param current_state: 当前对话状态机状态（确认门应答判定依赖它）
        :param has_active_task: 是否存在进行中任务（预留给续接判定）
        :return: IntentResult
        """
        normalized = (text or "").strip()
        if not normalized:
            # 空文本直接兜底为未知
            return IntentResult(
                pred_label=IntentLabel.META_UNKNOWN,
                confidence=0.0,
                decision_source=DecisionSource.RULE_FALLBACK,
            )

        # 控制层：取消订单 / META / 确认门 / 纯槽位（与混合分类器共用）
        control = self.classify_control(
            normalized, current_state=current_state, has_active_task=has_active_task
        )
        if control is not None:
            return control

        lowered = normalized.lower()
        # 按优先级遍历关键词表，命中即返回
        for label, keywords in _KEYWORDS:
            for kw in keywords:
                if kw in normalized or kw in lowered:
                    return IntentResult(
                        pred_label=label,
                        confidence=_KEYWORD_CONFIDENCE,
                        decision_source=DecisionSource.RULE_KEYWORD,
                    )

        # 低置信兜底为未知
        return IntentResult(
            pred_label=IntentLabel.META_UNKNOWN,
            confidence=0.3,
            decision_source=DecisionSource.RULE_FALLBACK,
        )

    def classify_control(
        self,
        normalized: str,
        *,
        current_state: str = DialogStateValue.IDLE,
        has_active_task: bool = False,
    ) -> IntentResult | None:
        """控制层判定：只处理必须确定性执行的意图，未命中返回 None。

        供 HybridIntentClassifier 复用（taxonomy 第 8 节三层架构的第 1 层）：
        取消订单正则、META 控制关键词（转人工/放弃/身份）、
        确认门应答（仅 CONFIRMING）、纯槽位输入。
        业务意图不在此判定——混合模式下交给语义模型。
        """
        lowered = normalized.lower()

        # —— 取消订单：带业务宾语，必须先于裸「取消」（META.ABORT）判定 ——
        if _CANCEL_ORDER_RE.search(normalized):
            return IntentResult(
                pred_label=IntentLabel.ORDER_CANCEL,
                confidence=_KEYWORD_CONFIDENCE,
                decision_source=DecisionSource.RULE_KEYWORD,
            )

        # —— 确认门应答：仅在 CONFIRMING 状态判定（上下文敏感意图）——
        # 注意放在 META 关键词之前：「不用了」在确认门下语义是否认而非放弃会话，
        # 但两者最终都会终止任务，先判 DENY 可以让决策日志语义更准确。
        if current_state == DialogStateValue.CONFIRMING:
            gate = self._match_confirm_gate(normalized, lowered)
            if gate is not None:
                return gate

        # —— META 控制类关键词（错判代价高，关键词精度足够，不交给模型）——
        for label, keywords in _META_CONTROL_KEYWORDS:
            for kw in keywords:
                if kw in normalized or kw in lowered:
                    return IntentResult(
                        pred_label=label,
                        confidence=_KEYWORD_CONFIDENCE,
                        decision_source=DecisionSource.RULE_KEYWORD,
                    )

        # —— 纯槽位输入：整句基本只包含订单号或手机号 ——
        if self._is_slot_only(normalized):
            return IntentResult(
                pred_label=IntentLabel.META_SLOT_ONLY,
                confidence=_SLOT_ONLY_CONFIDENCE,
                decision_source=DecisionSource.RULE_SLOT_ONLY,
            )

        return None

    @staticmethod
    def _match_confirm_gate(normalized: str, lowered: str) -> IntentResult | None:
        """确认门应答判定：短文本 + 确认/否认词命中。

        限制长度（<= 10 字符）避免把「好的，另外我还想问下物流」这类
        带新诉求的长句误吞成纯确认——长句交给后续关键词/兜底逻辑处理。
        """
        if len(normalized) > 10:
            return None
        # 先判否认：否认词多为「不 + 确认词」结构（如「不确认」「不用」），
        # 若先判确认会把「不确认」误命中「确认」
        for word in _DENY_WORDS:
            if word in normalized or word in lowered:
                return IntentResult(
                    pred_label=IntentLabel.META_DENY,
                    confidence=_CONFIRM_GATE_CONFIDENCE,
                    decision_source=DecisionSource.RULE_CONFIRM_GATE,
                )
        for word in _CONFIRM_WORDS:
            if word in normalized or word in lowered:
                return IntentResult(
                    pred_label=IntentLabel.META_CONFIRM,
                    confidence=_CONFIRM_GATE_CONFIDENCE,
                    decision_source=DecisionSource.RULE_CONFIRM_GATE,
                )
        return None

    @staticmethod
    def _is_slot_only(text: str) -> bool:
        """判断输入是否为纯槽位（去掉常见前后缀后仅剩订单号或手机号）。

        必须至少包含一位数字：订单号 / 手机号必含数字，
        避免 "thank you" 这类纯字母短语被误判为槽位输入。
        """
        # 去掉常见提示词与标点，只看主体
        stripped = re.sub(r"(订单号|订单|单号|order|手机号|电话|号码|[：:\s,，。.])", "", text, flags=re.I)
        if not any(ch.isdigit() for ch in stripped):
            return False
        return bool(ORDER_ID_ONLY_RE.fullmatch(stripped) or PHONE_ONLY_RE.fullmatch(stripped))


# 模块级单例
rule_intent_classifier = RuleIntentClassifier()
