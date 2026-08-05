"""Skill 注册表（SkillResolver 第一版）。

把意图标签映射到 Skill 能力声明。第一版用内存静态注册，
后续阶段再支持从 Skill md 文件加载（见 docs/prompts/skill_and_guardrails_standard.md）。
所有回复遵守全局护栏：不编造价格 / 退款结果，不假装真人。
"""

from app.chat.intent.types import IntentLabel
from app.chat.skills.types import Skill, SkillKind

# 意图 → Skill 静态注册表
_SKILLS: dict[str, Skill] = {
    # —— 商品（读操作）——
    IntentLabel.PRODUCT_ASK_PRICE: Skill(
        skill_id="product_ask_price",
        name="商品价格咨询",
        domain="PRODUCT",
        intent=IntentLabel.PRODUCT_ASK_PRICE,
        kind=SkillKind.READ,
        required_slots=["product_name"],
        templates={
            "collect": "请问您想咨询哪款商品的价格？",
            "answer": "好的，您想咨询「{product_name}」的价格，我帮您核实后回复，请稍等。",
        },
    ),
    IntentLabel.PRODUCT_ASK_INFO: Skill(
        skill_id="product_ask_info",
        name="商品信息咨询",
        domain="PRODUCT",
        intent=IntentLabel.PRODUCT_ASK_INFO,
        kind=SkillKind.READ,
        required_slots=["product_name"],
        templates={
            "collect": "请问您想了解哪款商品呢？",
            "answer": "好的，您想了解「{product_name}」的信息，我为您整理后回复。",
        },
    ),
    IntentLabel.PRODUCT_ASK_STOCK: Skill(
        skill_id="product_ask_stock",
        name="商品库存咨询",
        domain="PRODUCT",
        intent=IntentLabel.PRODUCT_ASK_STOCK,
        kind=SkillKind.READ,
        required_slots=["product_name"],
        templates={
            "collect": "请问您想查询哪款商品的库存？",
            "answer": "好的，您想查询「{product_name}」的库存，我帮您核实后回复。",
        },
    ),
    # —— 商品扩展（读操作，SetFit 语义层可达）——
    # Stage 32 实做：空壳升级为真实选品/对比（槽位收集 + 商品库硬约束查询）
    "PRODUCT.COMPARE": Skill(
        skill_id="product_compare",
        name="商品对比",
        domain="PRODUCT",
        intent="PRODUCT.COMPARE",
        kind=SkillKind.READ,
        required_slots=["compare_items"],
        templates={
            "collect": "好的，请告诉我您想对比的两款商品名称，例如「凉风X1 和 凉风X2」。",
            "answer": "好的，我来为您对比这两款商品。",
        },
    ),
    "PRODUCT.RECOMMEND": Skill(
        skill_id="product_recommend",
        name="商品推荐",
        domain="PRODUCT",
        intent="PRODUCT.RECOMMEND",
        kind=SkillKind.READ,
        required_slots=["category", "budget"],
        templates={
            "collect": "好的，我来帮您挑！请告诉我想看的品类和大概预算，例如「风扇，预算300以内」（已说过的不用重复）。",
            "answer": "好的，我按您的需求为您筛选商品。",
        },
    ),
    # —— 订单 / 物流（读操作）——
    IntentLabel.ORDER_QUERY_STATUS: Skill(
        skill_id="order_query_status",
        name="订单状态查询",
        domain="ORDER",
        intent=IntentLabel.ORDER_QUERY_STATUS,
        kind=SkillKind.READ,
        required_slots=["order_id"],
        templates={
            "collect": "请提供您要查询的订单号。",
            "answer": "好的，您要查询订单「{order_id}」的状态，我帮您核实后回复。",
        },
    ),
    "ORDER.CREATE": Skill(
        skill_id="order_create",
        name="下单引导",
        domain="ORDER",
        intent="ORDER.CREATE",
        kind=SkillKind.READ,
        required_slots=["product_name"],
        templates={
            "collect": "请问您想购买哪款商品？",
            "answer": "好的，您想购买「{product_name}」，我为您确认商品信息后引导下单。",
        },
    ),
    IntentLabel.LOGISTICS_TRACK: Skill(
        skill_id="logistics_track",
        name="物流跟踪",
        domain="LOGISTICS",
        intent=IntentLabel.LOGISTICS_TRACK,
        kind=SkillKind.READ,
        required_slots=["order_id"],
        templates={
            "collect": "请提供需要查询物流的订单号。",
            "answer": "好的，您要查询订单「{order_id}」的物流，我帮您核实后回复。",
        },
    ),
    "LOGISTICS.DELIVERY_TIME": Skill(
        skill_id="logistics_delivery_time",
        name="发货/送达时间",
        domain="LOGISTICS",
        intent="LOGISTICS.DELIVERY_TIME",
        kind=SkillKind.READ,
        required_slots=["order_id"],
        templates={
            "collect": "请提供订单号，我帮您查询发货和预计送达时间。",
            "answer": "好的，您要查询订单「{order_id}」的发货/送达时间，我帮您核实后回复。",
        },
    ),
    "LOGISTICS.SHIPPING_FEE": Skill(
        skill_id="logistics_shipping_fee",
        name="运费咨询",
        domain="LOGISTICS",
        intent="LOGISTICS.SHIPPING_FEE",
        kind=SkillKind.READ,
        required_slots=[],
        templates={
            "answer": "请告诉我您的收货地区和购买的商品，我帮您核实运费和包邮条件。",
        },
    ),
    # —— 改地址（写操作，进确认门；完整槽位收集在 Stage 05 工具层落地）——
    "ORDER.CHANGE_ADDRESS": Skill(
        skill_id="order_change_address",
        name="修改收货信息",
        domain="ORDER",
        intent="ORDER.CHANGE_ADDRESS",
        kind=SkillKind.WRITE,
        required_slots=["order_id"],
        templates={
            "collect": "请提供需要修改收货信息的订单号。",
            "confirm": "您要修改订单「{order_id}」的收货信息，确认提交吗？（回复“确认”继续，回复“不用”放弃）",
            "confirmed": "已受理订单「{order_id}」的收货信息修改申请，具体新地址将由人工与您核实后变更。",
        },
    ),
    # —— 订单取消（写操作，进确认门，不直接执行）——
    IntentLabel.ORDER_CANCEL: Skill(
        skill_id="order_cancel",
        name="取消订单",
        domain="ORDER",
        intent=IntentLabel.ORDER_CANCEL,
        kind=SkillKind.WRITE,
        required_slots=["order_id"],
        templates={
            "collect": "请提供需要取消的订单号。",
            "confirm": "您要取消订单「{order_id}」，确认提交吗？（回复“确认”继续，回复“不用”放弃）",
            "confirmed": "已受理您对订单「{order_id}」的取消申请，我们核实订单状态后会尽快为您处理并反馈结果。",
        },
    ),
    # —— 会员注册（Stage 33：写操作，进确认门；规则层触发 + NBA 主动建议入口）——
    IntentLabel.MEMBER_REGISTER: Skill(
        skill_id="member_register",
        name="会员注册",
        domain="MEMBER",
        intent=IntentLabel.MEMBER_REGISTER,
        kind=SkillKind.WRITE,
        required_slots=["phone"],
        templates={
            "collect": "好的，我来帮您开通会员！请提供用于注册的手机号。",
            "confirm": "将以手机号「{phone}」为您注册本平台会员，确认提交吗？（回复“确认”继续，回复“不用”放弃）",
            "confirmed": "已为您提交会员注册，开通结果以短信通知为准～",
        },
    ),
    # —— 售后（写操作，进确认门，不直接执行）——
    IntentLabel.AFTERSALE_REFUND: Skill(
        skill_id="aftersale_refund",
        name="售后退款",
        domain="AFTERSALE",
        intent=IntentLabel.AFTERSALE_REFUND,
        kind=SkillKind.WRITE,
        required_slots=["order_id"],
        templates={
            "collect": "请提供需要退款的订单号。",
            "confirm": "您要对订单「{order_id}」申请退款，确认提交吗？（回复“确认”继续，回复“不用”放弃）",
            "confirmed": "已受理您对订单「{order_id}」的退款申请，我们核实后会尽快为您处理并反馈结果。",
        },
    ),
    IntentLabel.AFTERSALE_RETURN: Skill(
        skill_id="aftersale_return",
        name="售后退货",
        domain="AFTERSALE",
        intent=IntentLabel.AFTERSALE_RETURN,
        kind=SkillKind.WRITE,
        required_slots=["order_id"],
        templates={
            "collect": "请提供需要退货的订单号。",
            "confirm": "您要对订单「{order_id}」申请退货，确认提交吗？（回复“确认”继续，回复“不用”放弃）",
            "confirmed": "已受理您对订单「{order_id}」的退货申请，我们核实后会尽快为您处理并反馈结果。",
        },
    ),
    IntentLabel.AFTERSALE_EXCHANGE: Skill(
        skill_id="aftersale_exchange",
        name="售后换货",
        domain="AFTERSALE",
        intent=IntentLabel.AFTERSALE_EXCHANGE,
        kind=SkillKind.WRITE,
        required_slots=["order_id"],
        templates={
            "collect": "请提供需要换货的订单号。",
            "confirm": "您要对订单「{order_id}」申请换货，确认提交吗？（回复“确认”继续，回复“不用”放弃）",
            "confirmed": "已受理您对订单「{order_id}」的换货申请，我们核实后会尽快为您处理并反馈结果。",
        },
    ),
    "AFTERSALE.REPAIR": Skill(
        skill_id="aftersale_repair",
        name="维修/保修",
        domain="AFTERSALE",
        intent="AFTERSALE.REPAIR",
        kind=SkillKind.WRITE,
        required_slots=["order_id"],
        templates={
            "collect": "请提供需要报修商品的订单号。",
            "confirm": "您要对订单「{order_id}」的商品申请维修，确认提交吗？（回复“确认”继续，回复“不用”放弃）",
            "confirmed": "已受理您对订单「{order_id}」的报修申请，我们核实保修情况后会尽快联系您。",
        },
    ),
    IntentLabel.AFTERSALE_COMPLAIN: Skill(
        skill_id="aftersale_complain",
        name="投诉",
        domain="AFTERSALE",
        intent=IntentLabel.AFTERSALE_COMPLAIN,
        kind=SkillKind.META,
        required_slots=[],
        templates={
            "answer": "非常抱歉给您带来不便，我已记录您的问题，必要时会为您转接人工客服。",
        },
    ),
    # —— META 控制类 ——
    IntentLabel.META_TRANSFER_HUMAN: Skill(
        skill_id="meta_transfer_human",
        name="转人工",
        domain="META",
        intent=IntentLabel.META_TRANSFER_HUMAN,
        kind=SkillKind.META,
        templates={"answer": "好的，我会为您转接人工客服，请稍等。"},
    ),
    # 确认门否认：用户在 CONFIRMING 下拒绝执行（v2 新增，修复确认门死循环）
    IntentLabel.META_DENY: Skill(
        skill_id="meta_deny",
        name="确认门否认",
        domain="META",
        intent=IntentLabel.META_DENY,
        kind=SkillKind.META,
        templates={"answer": "好的，本次操作不会提交。还有什么可以帮您？"},
    ),
    IntentLabel.META_BOT_IDENTITY: Skill(
        skill_id="meta_bot_identity",
        name="身份询问",
        domain="META",
        intent=IntentLabel.META_BOT_IDENTITY,
        kind=SkillKind.META,
        # 护栏：不假装真人
        templates={"answer": "我是智能客服助手，很高兴为您服务～"},
    ),
    IntentLabel.META_ABORT: Skill(
        skill_id="meta_abort",
        name="取消",
        domain="META",
        intent=IntentLabel.META_ABORT,
        kind=SkillKind.META,
        templates={"answer": "好的，已为您取消当前操作。还有什么可以帮您？"},
    ),
    IntentLabel.META_UNKNOWN: Skill(
        skill_id="meta_unknown",
        name="未知意图",
        domain="META",
        intent=IntentLabel.META_UNKNOWN,
        kind=SkillKind.META,
        templates={
            "answer": "我需要再确认一下，您是想咨询商品、订单、物流还是售后问题？",
        },
    ),
    # —— 支付 / 营销（SetFit 语义层可达）——
    "PAYMENT.METHOD": Skill(
        skill_id="payment_method",
        name="支付方式咨询",
        domain="PAYMENT",
        intent="PAYMENT.METHOD",
        kind=SkillKind.READ,
        required_slots=[],
        templates={
            "answer": "我们支持常见的在线支付方式，具体以结算页展示为准；分期等特殊方式我帮您核实后回复。",
        },
    ),
    "PAYMENT.ISSUE": Skill(
        skill_id="payment_issue",
        name="支付异常",
        domain="PAYMENT",
        intent="PAYMENT.ISSUE",
        kind=SkillKind.META,
        required_slots=[],
        templates={
            # 按 skills_design：支付异常涉及资金，全部转人工核实，不自动处理
            "answer": "支付相关的异常我已记录，为避免资金风险，将由人工客服为您核实处理，请稍等。",
        },
    ),
    "PAYMENT.INVOICE": Skill(
        skill_id="payment_invoice",
        name="发票申请",
        domain="PAYMENT",
        intent="PAYMENT.INVOICE",
        kind=SkillKind.WRITE,
        required_slots=["order_id"],
        templates={
            "collect": "请提供需要开发票的订单号。",
            "confirm": "您要为订单「{order_id}」申请开票，确认提交吗？（回复“确认”继续，回复“不用”放弃）",
            "confirmed": "已受理订单「{order_id}」的开票申请，发票抬头等信息将由人工与您核实后开具。",
        },
    ),
    "PROMOTION.COUPON": Skill(
        skill_id="promotion_coupon",
        name="优惠券咨询",
        domain="PROMOTION",
        intent="PROMOTION.COUPON",
        kind=SkillKind.READ,
        required_slots=[],
        templates={
            "answer": "好的，我帮您查询账户可用优惠券和使用条件，核实后回复您。",
        },
    ),
    "PROMOTION.ACTIVITY": Skill(
        skill_id="promotion_activity",
        name="活动咨询",
        domain="PROMOTION",
        intent="PROMOTION.ACTIVITY",
        kind=SkillKind.READ,
        required_slots=[],
        templates={
            "answer": "好的，我帮您核实当前的活动信息，以活动页面规则为准，稍后回复您。",
        },
    ),
    # —— FAQ 知识问答（读操作，无必填槽位；回复由 rag_answer 节点走 RAG 生成，
    # KB 关闭或检索失败时降级用本模板）——
    IntentLabel.FAQ_GENERAL: Skill(
        skill_id="faq_general",
        name="平台政策与通用知识问答",
        domain="FAQ",
        intent=IntentLabel.FAQ_GENERAL,
        kind=SkillKind.READ,
        required_slots=[],
        templates={
            "answer": "这个问题我需要进一步核实后回复您，您也可以换个说法再问我一次。",
        },
    ),
    # —— 闲聊 ——
    IntentLabel.CHITCHAT_GENERAL: Skill(
        skill_id="chitchat_general",
        name="闲聊",
        domain="CHITCHAT",
        intent=IntentLabel.CHITCHAT_GENERAL,
        kind=SkillKind.CHITCHAT,
        templates={"answer": "您好，有什么可以帮您？"},
    ),
    IntentLabel.CHITCHAT_THANKS: Skill(
        skill_id="chitchat_thanks",
        name="致谢",
        domain="CHITCHAT",
        intent=IntentLabel.CHITCHAT_THANKS,
        kind=SkillKind.CHITCHAT,
        templates={"answer": "不客气，随时为您服务！"},
    ),
}

# 兜底 Skill（找不到对应意图时使用）
_FALLBACK_SKILL = _SKILLS[IntentLabel.META_UNKNOWN]

# —— Stage 05：启动时把 skills/ md 的能力声明（工具/动作/风险等级等）
# 合并进注册表；校验失败（SkillLoadError）直接抛出，禁止带病上线 ——
from app.chat.skills.loader import apply_declarations  # noqa: E402  循环导入规避：loader 不依赖本模块

apply_declarations(_SKILLS)


class SkillRegistry:
    """Skill 解析器：根据意图标签返回 Skill。"""

    def get(self, intent_label: str) -> Skill:
        """按意图返回 Skill，未注册时返回兜底 Skill。"""
        return _SKILLS.get(intent_label, _FALLBACK_SKILL)


# 模块级单例
skill_registry = SkillRegistry()
