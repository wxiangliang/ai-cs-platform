"""意图目录：意图码 → 一句话描述（LLM 分类 prompt 的程序化来源）。

与 docs/chat/intent_taxonomy.md 第 3 节注册表保持一致（单一事实来源在文档，
本文件是其代码投影；新增/修改意图必须先改 taxonomy 再同步这里）。
按需求约束：LLM prompt 中的意图清单必须从本注册表程序化生成，禁止手抄进 prompt 文件。
"""

# SetFit 语义层的 29 个可判定意图（上下文敏感 META 意图不在此列，由规则判定）
INTENT_DESCRIPTIONS: dict[str, str] = {
    "PRODUCT.ASK_INFO": "商品介绍/参数/属性咨询（针对具体商品）",
    "PRODUCT.ASK_PRICE": "商品询价/议价",
    "PRODUCT.ASK_STOCK": "商品库存/有无现货",
    "PRODUCT.COMPARE": "多个商品对比",
    "PRODUCT.RECOMMEND": "商品推荐/选购建议",
    "ORDER.QUERY_STATUS": "订单状态查询（是否付款/是否发货）",
    "ORDER.CREATE": "想购买/下单意向",
    "ORDER.CANCEL": "取消还没发货的订单",
    "ORDER.CHANGE_ADDRESS": "修改收货地址/收件人信息",
    "LOGISTICS.TRACK": "包裹到哪了/物流轨迹",
    "LOGISTICS.DELIVERY_TIME": "什么时候发货/什么时候能到",
    "LOGISTICS.SHIPPING_FEE": "运费多少/是否包邮",
    "AFTERSALE.REFUND": "要求退款（诉求是钱）",
    "AFTERSALE.RETURN": "要求退货（收到货要寄回）",
    "AFTERSALE.EXCHANGE": "要求换货（换规格/换新）",
    "AFTERSALE.REPAIR": "报修/维修保修申请",
    "AFTERSALE.COMPLAIN": "投诉/表达强烈不满",
    "PAYMENT.METHOD": "支付方式/分期咨询",
    "PAYMENT.ISSUE": "支付失败/重复扣款等支付异常",
    "PAYMENT.INVOICE": "开发票",
    "PROMOTION.COUPON": "优惠券查询/使用问题",
    "PROMOTION.ACTIVITY": "促销活动咨询",
    "FAQ.GENERAL": "平台政策/规则类问答（退换货政策、保修条款、会员积分规则等，不绑定具体订单商品）",
    "CHITCHAT.GENERAL": "打招呼/闲聊",
    "CHITCHAT.THANKS": "表达感谢",
    "META.TRANSFER_HUMAN": "要求转人工客服",
    "META.BOT_IDENTITY": "询问是不是机器人/真人",
    "META.ABORT": "算了/不用了，放弃当前操作（不带业务宾语）",
    "META.UNKNOWN": "无法归入以上任何意图",
}

# 易混淆意图边界裁决规则（taxonomy 第 6 节的 prompt 版本，注入 LLM 二判）
BOUNDARY_RULES = """易混淆意图的裁决规则：
1. 「取消订单/取消这单」（宾语是订单）→ ORDER.CANCEL；裸「算了/不用了」→ META.ABORT。
2. 退款/退货/取消：未发货想终止订单→ORDER.CANCEL；收到货要寄回→AFTERSALE.RETURN；只谈退钱→AFTERSALE.REFUND。
3. 物流：问「到哪了/轨迹」→LOGISTICS.TRACK；问「什么时候发货/送达」→LOGISTICS.DELIVERY_TIME；问「订单什么状态/付款发货没」→ORDER.QUERY_STATUS。
4. 商品信息 vs 政策问答：绑定具体商品的参数/价格→PRODUCT.*；平台级政策/规则总述（含"是什么意思/什么规定"句式）→FAQ.GENERAL。
5. 「保修政策是什么/保修多久」这类政策疑问→FAQ.GENERAL；「我的东西坏了要修」这类动作请求→AFTERSALE.REPAIR。"""
