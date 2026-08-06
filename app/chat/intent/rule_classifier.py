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
from typing import Any

from app.chat.intent.types import DecisionSource, IntentLabel, IntentResult
from app.chat.slots.patterns import ORDER_ID_ONLY_RE, PHONE_ONLY_RE
from app.chat.slots.pending_fill import try_fill_pending_slot
from app.chat.state.types import DialogStateValue

# 「取消订单」类表达：宾语是订单 → 业务意图 ORDER.CANCEL，
# 必须先于 META.ABORT 的裸「取消」关键词判定（taxonomy 第 4 节裁决规则 1）。
# 被动/完成式（「订单被取消了」「订单已取消」）是状态咨询不是取消请求：
# 「取消」前排除「被/已」，「订单…取消」中间不得隔「被/已」。
_CANCEL_ORDER_RE = re.compile(
    r"((?<![被已])取消.{0,4}(订单|这单|那单|单子))|((订单|这单|那单)[^被已]{0,4}取消)|(退订)"
)

# 预约（Stage 39，确定性触发——语义层无训练样本，规则层是主入口）：
# CANCEL 先于 BOOK 判（「取消预约」含「预约」）；被动/完成式（「预约被取消了」）
# 是状态咨询不触发（cancel-order 正则同款防误伤）
_APPOINTMENT_CANCEL_RE = re.compile(
    r"((?<![被已])取消.{0,4}预约)|(预约[^被已]{0,4}取消)"
)
_APPOINTMENT_BOOK_RE = re.compile(
    r"(?:预约|帮我约|约个|约一下)\s*.{0,6}?(?:安装|维修|取件|上门|回访|演示|师傅|门店)"
    r"|(?:安装|维修|取件|上门|回访|演示)\s*.{0,4}?预约"
    r"|^(?:我要|帮我|想)?预约$"
)

# 会员注册（Stage 33，确定性触发——语义层无训练样本，规则层是主入口）：
# 「注册/开通 + 会员」任意近邻顺序，或裸短句「注册」（NBA 建议话术引导
# 用户显式回复「注册」）。两条防误伤（否定/第三方平台）见 _match_member_register
_MEMBER_REGISTER_RE = re.compile(
    r"(?:注册|开通)\s*.{0,4}?会员|会员\s*.{0,4}?(?:注册|开通)|^(?:我要|帮我|想)?注册$"
)
_MEMBER_NEGATION_RE = re.compile(r"不想|不要|不用|别|先不|暂不|取消")
_THIRD_PARTY_RE = re.compile(r"抖音|微信|淘宝|京东|支付宝|微博|小红书|快手|app|账号|网站|平台")

# META 控制类关键词（控制层专用，最高优先级）。
# 顺序即优先级：身份询问必须先于转人工——「你是真人吗」问的是身份，
# 若先判转人工会被「真人」子串误吞成建单。
# META.ABORT 不在此表：裸「算了/取消/结束」在长句里常是转折词而非放弃指令，
# 需经 _is_pure_abort 纯放弃判定，见 classify_control。
_META_CONTROL_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (IntentLabel.META_BOT_IDENTITY, ("你是机器人", "你是真人", "你是谁", "是不是机器人", "人工还是机器")),
    (IntentLabel.META_TRANSFER_HUMAN, ("转人工", "人工客服", "找客服", "真人", "客服电话")),
]

# 放弃会话关键词（配合纯放弃判定使用，不做裸子串命中）
_ABORT_WORDS = ("算了", "不用了", "不需要", "没事了", "取消", "结束")
# 纯放弃判定的语气/连接成分：去掉放弃词后，剩余字符全部落在此集合内才算纯放弃。
# 「算了，然后都不用了」→ 剩「然后都」→ 纯放弃；
# 「算了，还是帮我退款吧」→ 剩「帮我退款」→ 带业务诉求，放行给语义层。
_ABORT_FILLER_CHARS = frozenset("，。！？；、!?,.;: 那就先再都也还是然后吧啊呢哦嗯了的哈好呀喔嘛谢多您你我")

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

# 任务中途否定（Stage 23，仅 COLLECTING 状态判定）：用户在补槽过程中否定
# 方向本身（「不是要退货」）而非提供槽位。含数字不判——那是槽位纠正
# （「不对，是 A12345678」）；限长防误吞长句新诉求
_TASK_DENY_RE = re.compile(
    r"(我)?不是(要|想|说)|搞错了|理解错|弄错了|不是这个意思|谁(要|说)"
)
_TASK_DENY_BARE = ("不对", "不是")
_TASK_DENY_MAX_LEN = 12

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
        pending_slot: str | None = None,
        collected_slots: dict[str, Any] | None = None,
    ) -> IntentResult:
        """对预处理后的文本做意图识别。

        :param text: 归一化后的用户文本
        :param current_state: 当前对话状态机状态（确认门应答判定依赖它）
        :param has_active_task: 是否存在进行中任务（预留给续接判定）
        :param pending_slot: 当前任务第一个缺失槽位（Stage 26 补槽守护，COLLECTING 时传入）
        :param collected_slots: 当前任务已收集槽位（pending fill 值级冲突检测）
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

        # 控制层：取消订单 / META / 确认门 / 纯槽位 / pending fill（与混合分类器共用）
        control = self.classify_control(
            normalized,
            current_state=current_state,
            has_active_task=has_active_task,
            pending_slot=pending_slot,
            collected_slots=collected_slots,
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
        pending_slot: str | None = None,
        collected_slots: dict[str, Any] | None = None,
    ) -> IntentResult | None:
        """控制层判定：只处理必须确定性执行的意图，未命中返回 None。

        供 HybridIntentClassifier 复用（taxonomy 第 8 节三层架构的第 1 层）：
        取消订单正则、META 控制关键词（转人工/放弃/身份）、
        确认门应答（仅 CONFIRMING）、纯槽位输入、pending-slot 定向提取
        （Stage 26 补槽守护，排在全部控制语义之后——顺序红线见 stage-26 文档 4.1）。
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

        # —— 任务中途否定：仅在 COLLECTING 状态判定（Stage 23 方向纠偏）——
        # 「不对，我不是要退货」不能被当作 UNKNOWN 吞掉继续追问槽位；
        # 判 META.DENY 由状态机仅终止当前任务（不像 META.ABORT 清空一切）
        if current_state == DialogStateValue.COLLECTING and self._is_task_deny(normalized):
            return IntentResult(
                pred_label=IntentLabel.META_DENY,
                confidence=_CONFIRM_GATE_CONFIDENCE,
                decision_source=DecisionSource.RULE_TASK_DENY,
            )

        # —— 预约（Stage 39）：CANCEL 先判（「取消预约」含「预约」）——
        if _APPOINTMENT_CANCEL_RE.search(normalized):
            return IntentResult(
                pred_label=IntentLabel.APPOINTMENT_CANCEL,
                confidence=_KEYWORD_CONFIDENCE,
                decision_source=DecisionSource.RULE_KEYWORD,
            )
        if _APPOINTMENT_BOOK_RE.search(normalized) and not _MEMBER_NEGATION_RE.search(
            normalized
        ):
            return IntentResult(
                pred_label=IntentLabel.APPOINTMENT_BOOK,
                confidence=_KEYWORD_CONFIDENCE,
                decision_source=DecisionSource.RULE_KEYWORD,
            )

        # —— 会员注册（Stage 33）：置于确认门/任务否定之后（CONFIRMING 下
        # 「确认/不用」优先），否定语境（「不想开通会员」）与第三方平台语境
        # （「怎么注册抖音账号」是 OOS 不是本平台会员）都不触发 ——
        if (
            _MEMBER_REGISTER_RE.search(normalized)
            and not _MEMBER_NEGATION_RE.search(normalized)
            and not _THIRD_PARTY_RE.search(lowered)
        ):
            return IntentResult(
                pred_label=IntentLabel.MEMBER_REGISTER,
                confidence=_KEYWORD_CONFIDENCE,
                decision_source=DecisionSource.RULE_KEYWORD,
            )

        # —— META 控制类关键词（身份询问先于转人工，见词表处注释）——
        for label, keywords in _META_CONTROL_KEYWORDS:
            for kw in keywords:
                if kw in normalized or kw in lowered:
                    return IntentResult(
                        pred_label=label,
                        confidence=_KEYWORD_CONFIDENCE,
                        decision_source=DecisionSource.RULE_KEYWORD,
                    )

        # —— 放弃会话：必须整句是纯放弃表达才短路，长句夹带诉求放行给语义层 ——
        # （「怎么取消自动续费」「活动什么时候结束」是咨询，不是放弃）
        if any(w in normalized for w in _ABORT_WORDS) and self._is_pure_abort(normalized):
            return IntentResult(
                pred_label=IntentLabel.META_ABORT,
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

        # —— pending-slot 定向提取（Stage 26 补槽守护）：COLLECTING 下且任务在等
        # 某个槽位时，「订单号是12345678」这类回答式消息直接续接当前任务，
        # 不进 SetFit 全表分类（防高置信误判成新意图、挂起当前任务）。
        # 必须排在所有显式控制语义之后：「订单号先不找了，帮我查运费」
        # 要先被放弃/否定语义接走 ——
        if (
            pending_slot
            and current_state == DialogStateValue.COLLECTING
            and (fill := try_fill_pending_slot(normalized, pending_slot, collected_slots))
        ):
            return IntentResult(
                pred_label=IntentLabel.META_SLOT_ONLY,
                confidence=_SLOT_ONLY_CONFIDENCE,
                decision_source=DecisionSource.RULE_PENDING_SLOT,
                pending_fill={"slot": fill.slot, "value": fill.value, "evidence": fill.evidence},
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
    def _is_task_deny(text: str) -> bool:
        """任务中途否定判定（仅 COLLECTING 调用）。

        含数字不判（槽位纠正「不对，是 A123」）；命中否定模式后做残差判定
        （与纯放弃同纪律）：去掉否定表达与被否定的宾语后仍有实质内容
        （「不是要退货，我想查物流」）→ 不判否定，交给语义层拆出新意图；
        裸「不对/不是」只认极短句。
        """
        if len(text) > _TASK_DENY_MAX_LEN or any(ch.isdigit() for ch in text):
            return False
        stripped = text.strip("，。！？!?,. ")
        if stripped in _TASK_DENY_BARE:
            return True
        m = _TASK_DENY_RE.search(text)
        if not m:
            return False
        # 残差：否定模式之后最多允许 2-3 字宾语（「不是要退货」），
        # 再长说明句中带着新诉求，放行语义层
        residue = text[m.end():].strip("，。！？!?,. 的了呢啊吧")
        return len(residue) <= 3

    @staticmethod
    def _is_pure_abort(text: str) -> bool:
        """纯放弃判定：去掉放弃词后，剩余字符全部是语气/连接成分才算放弃。

        防止「算了/取消/结束/不用了」在长句中作转折词时误吞用户真实诉求
        （确认门应答判定用 ≤10 字符护栏，这里同理但按内容判：
        长句纯放弃「算了，然后都不用了」仍要能判 ABORT）。
        """
        residue = text
        for word in _ABORT_WORDS:
            residue = residue.replace(word, "")
        return all(ch in _ABORT_FILLER_CHARS for ch in residue)

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
