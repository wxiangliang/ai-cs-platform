"""意图类型定义。

集中声明第一版支持的意图标签常量，以及意图识别结果数据结构 IntentResult。
意图采用 DOMAIN.ACTION 形式，便于后续按 domain 路由 Skill。
"""

from dataclasses import dataclass, field
from typing import Any


class IntentLabel:
    """第一版支持的意图标签常量。"""

    # 商品类（读操作）
    PRODUCT_ASK_PRICE = "PRODUCT.ASK_PRICE"
    PRODUCT_ASK_INFO = "PRODUCT.ASK_INFO"
    PRODUCT_ASK_STOCK = "PRODUCT.ASK_STOCK"
    # 订单 / 物流（读操作）
    ORDER_QUERY_STATUS = "ORDER.QUERY_STATUS"
    LOGISTICS_TRACK = "LOGISTICS.TRACK"
    # 订单取消（写操作，需确认门）。注意与 META.ABORT（放弃当前会话操作）区分：
    # 「取消订单」的宾语是订单 → ORDER.CANCEL；裸「算了/取消」→ META.ABORT
    ORDER_CANCEL = "ORDER.CANCEL"
    # 售后类（高风险 / 写操作，需确认门）
    AFTERSALE_REFUND = "AFTERSALE.REFUND"
    AFTERSALE_RETURN = "AFTERSALE.RETURN"
    AFTERSALE_EXCHANGE = "AFTERSALE.EXCHANGE"
    AFTERSALE_COMPLAIN = "AFTERSALE.COMPLAIN"
    # 元意图（控制类，优先级高）。命名以 docs/chat/intent_taxonomy.md 为准，
    # 旧名 META.HANDOFF_REQUEST 已废弃（taxonomy 2.1 别名对照表）。
    META_TRANSFER_HUMAN = "META.TRANSFER_HUMAN"
    META_BOT_IDENTITY = "META.BOT_IDENTITY"
    META_ABORT = "META.ABORT"
    META_SLOT_ONLY = "META.SLOT_ONLY"
    META_UNKNOWN = "META.UNKNOWN"
    # 确认门应答意图：仅在对话状态为 CONFIRMING 时由分类器输出（上下文敏感）
    META_CONFIRM = "META.CONFIRM"
    META_DENY = "META.DENY"
    # FAQ 知识问答（平台政策/规则类，不绑定具体订单和商品，RAG 主入口）
    FAQ_GENERAL = "FAQ.GENERAL"
    # 闲聊
    CHITCHAT_GENERAL = "CHITCHAT.GENERAL"
    CHITCHAT_THANKS = "CHITCHAT.THANKS"


class DecisionSource:
    """意图决策来源标识。"""

    RULE_KEYWORD = "RULE_KEYWORD"  # 关键词命中
    RULE_SLOT_ONLY = "RULE_SLOT_ONLY"  # 纯槽位输入（如只发订单号）
    RULE_PENDING_SLOT = "RULE_PENDING_SLOT"  # pending-slot 定向提取命中（Stage 26 补槽守护）
    RULE_CONFIRM_GATE = "RULE_CONFIRM_GATE"  # 确认门应答（CONFIRMING 状态下的确认/否认）
    RULE_TASK_DENY = "RULE_TASK_DENY"  # 任务中途否定（COLLECTING 状态，Stage 23 方向纠偏）
    RULE_FALLBACK = "RULE_FALLBACK"  # 兜底未知
    # —— SetFit 语义层（Stage 04-02）——
    SETFIT = "SETFIT"  # SetFit 模型高置信命中
    SETFIT_LOW_CONF = "SETFIT_LOW_CONF"  # 模型低置信，兜底 UNKNOWN
    SETFIT_LOW_MARGIN = "SETFIT_LOW_MARGIN"  # 高分但 top1-top2 分差小、二判不可用时采纳（Stage 26，软确认接住）
    SETFIT_FALLBACK_RULE = "SETFIT_FALLBACK_RULE"  # 模型不可用，降级规则全表
    # —— LLM 层（Stage 04-01 / 05）——
    LLM = "LLM"  # SetFit 低置信难例的 LLM 二判命中
    LLM_CONFIRM_GATE = "LLM_CONFIRM_GATE"  # 确认门含糊应答的 LLM 解析（Stage 05）


@dataclass
class IntentResult:
    """意图识别结果。

    pred_label：预测意图标签
    confidence：置信度（0~1）
    decision_source：决策来源
    top_k：候选意图列表（第一版规则识别暂为空，预留入口）
    margin：SetFit top1-top2 分差（Stage 26；仅语义层来源有值，供路由与离线标定）
    pending_fill：pending-slot 定向提取证据 {slot, value, evidence}（Stage 26 补槽守护）
    """

    pred_label: str
    confidence: float
    decision_source: str
    top_k: list[dict[str, Any]] = field(default_factory=list)
    margin: float | None = None
    pending_fill: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为可落库的 dict（用于 decision_log.intent_result_json）。"""
        result = {
            "pred_label": self.pred_label,
            "confidence": self.confidence,
            "decision_source": self.decision_source,
            "top_k": self.top_k,
        }
        # Stage 26 证据字段：仅有值时落库，保持既有日志结构不膨胀
        if self.margin is not None:
            result["margin"] = self.margin
        if self.pending_fill is not None:
            result["pending_fill"] = self.pending_fill
        return result
