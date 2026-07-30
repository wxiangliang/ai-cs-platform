"""pending-slot 定向提取（Stage 26 补槽守护，需求见 stage-26 文档 4.1）。

已知当前任务只缺某个槽位时，判断本条消息是否就是该槽位的回答——
不做通用意图判断，只回答一个问题：「这句话里有没有该槽位的合法值」。
这比让 SetFit 在全表里重新分类简单得多，也稳定得多：
「订单号是12345678」不该被判成订单查询、误开新任务。

判定顺序红线（不得调换）：显式控制语义（确认门/任务否定/纯放弃/取消/转人工）
之后、SetFit 之前；未命中返回 None、零副作用，原样进入语义层。

证据分级（采纳策略见文档 4.1 表格）：
- explicit_slot_name：消息含 pending 槽位的名称提示词（「订单号是…」）；
- pure_value：整句就是裸值（大多已被更早的纯槽位判定接走，此处兜边界）；
- contextual_answer：回答式短句（「是…」「应该是…」），值须严格 fullmatch。
本层不用 LLM（llm_extracted 不作为高风险任务的续接依据）。

防误填规则（每条都有反例测试，tests/stage26/test_pending_fill.py）：
1. 类型冲突：等 order_id 却给了手机号形状的 11 位串 → 不填；
2. 显式槽位名冲突：消息说「手机号是…」而 pending 是 order_id → 不填；
3. 语境排除：值后紧跟量词/单位（「12345678个问题」）、前带金额符号 → 不填；
4. 残句判定：去掉值/槽位名/回答词/语气成分后仍有实质内容
   （新诉求、长句）→ 不填，放行语义层（含显式切换信号的情况天然被此规则覆盖）。
"""

import re
from dataclasses import dataclass

from app.chat.slots import patterns

# 各槽位的显式名称提示词（消息里出现且与 pending 槽位一致 → explicit_slot_name）
_SLOT_HINTS: dict[str, tuple[str, ...]] = {
    "order_id": ("订单号", "单号", "订单", "流水号", "order"),
    "phone": ("手机号", "手机", "电话", "号码"),
}

# 与 pending 槽位冲突的「别的字段」提示词：消息显式说了别的字段名 → 不是本槽位的回答
_CONFLICT_HINTS: dict[str, tuple[str, ...]] = {
    "order_id": ("手机号", "电话", "金额", "价格", "运费", "数量"),
    "phone": ("订单号", "单号", "流水号", "金额", "价格", "运费", "数量"),
}

# 值的合法性校验（fullmatch，复用 slots/patterns 单一事实来源）
_VALUE_RE: dict[str, re.Pattern[str]] = {
    "order_id": patterns.ORDER_ID_ONLY_RE,
    "phone": patterns.PHONE_ONLY_RE,
}

# 值后紧跟量词/单位 → 是数量/金额不是单号（语境排除）
_UNIT_AFTER = "个件台箱套双条只份包元块笔次张年月日号"
# 值前紧邻金额符号 → 金额不是单号
_MONEY_BEFORE = "¥￥$"

# 回答式引导词（contextual_answer 判型 + 残句剥离；长词在前防子串误剥）
_ANSWER_WORDS = ("应该是", "应该为", "大概是", "好像是", "可能是", "编号为", "就是", "是", "为")

# 残句语气/礼貌/连接成分：去掉值、槽位名、回答词后剩余字符全部落在此集合内
# 才算「槽位应答形」消息；否则句中带着别的实质内容，放行语义层
_FILLER_CHARS = frozenset("，。！？；、!?,.;:：#～~　 的了呢啊吧哈呀哦嗯哟您你好我那这个麻烦请谢多急快点尽下一")

# 候选值 token：连续字母数字串
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# 严格校验型槽位（供状态机 UNKNOWN 续接证据过滤，Stage 26）：
# 这些槽位的合法应答必然已在分类层被补槽守护/纯槽位判定接走，
# 能走到 UNKNOWN 的同型数字串（「我有12345678个问题」被通用正则误抽的
# order_id）一律不可信、不得并入任务；color 等无严格校验的槽位不在此列
STRICT_VALIDATED_SLOTS = frozenset(_VALUE_RE)


@dataclass
class PendingFillResult:
    """定向提取结果（落 intent_result.pending_fill 供决策日志与回放）。"""

    slot: str
    value: str
    evidence: str  # explicit_slot_name | pure_value | contextual_answer


def try_fill_pending_slot(
    text: str,
    pending_slot: str,
    collected: dict[str, object] | None = None,
) -> PendingFillResult | None:
    """判断消息是否为 pending 槽位的直接回答；未命中返回 None（零副作用）。

    :param text: 归一化后的用户文本
    :param pending_slot: 当前任务第一个缺失槽位名
    :param collected: 任务已收集槽位（值级冲突检测：别的槽位已存过同一值则不填）
    """
    normalized = (text or "").strip()
    value_re = _VALUE_RE.get(pending_slot)
    if not normalized or value_re is None:
        # 不认识的槽位类型不做定向提取（宁缺勿误填）
        return None

    # —— 防误填 2：显式说了别的字段名 → 不是本槽位的回答 ——
    if any(h in normalized for h in _CONFLICT_HINTS.get(pending_slot, ())):
        return None

    value = _pick_value(normalized, pending_slot, value_re, collected or {})
    if value is None:
        return None

    # —— 防误填 4：残句判定——去掉值/槽位名/回答词后必须只剩语气成分 ——
    residue = normalized.replace(value, "", 1)
    for hint in _SLOT_HINTS.get(pending_slot, ()):
        residue = residue.replace(hint, "")
    for word in _ANSWER_WORDS:
        residue = residue.replace(word, "")
    if not all(ch in _FILLER_CHARS for ch in residue.lower()):
        return None

    hints = _SLOT_HINTS.get(pending_slot, ())
    lowered = normalized.lower()
    if any(h in normalized or h in lowered for h in hints):
        evidence = "explicit_slot_name"
    elif normalized.strip("".join(_FILLER_CHARS)) == value:
        evidence = "pure_value"
    else:
        evidence = "contextual_answer"
    return PendingFillResult(slot=pending_slot, value=value, evidence=evidence)


def _pick_value(
    normalized: str,
    pending_slot: str,
    value_re: re.Pattern[str],
    collected: dict[str, object],
) -> str | None:
    """从消息中挑出 pending 槽位的合法值；类型/语境冲突一票否决。"""
    for match in _TOKEN_RE.finditer(normalized):
        token = match.group(0)
        if not value_re.fullmatch(token):
            continue
        # 订单号/手机号必含数字（与 _is_slot_only 同纪律）：
        # 防纯字母词（"please"）匹配 6-24 位字母数字形状被误当单号
        if not any(ch.isdigit() for ch in token):
            continue
        # —— 防误填 1：类型冲突——等 order_id 却给了手机号形状 → 整句不填 ——
        # （不是跳过这个 token：用户明确给了一个别的字段的值，交语义层理解）
        if pending_slot == "order_id" and patterns.PHONE_ONLY_RE.fullmatch(token):
            return None
        # —— 防误填 3：语境排除——量词/单位紧跟、金额符号紧邻 ——
        after = normalized[match.end() : match.end() + 1]
        if after and after in _UNIT_AFTER:
            return None
        before = normalized[match.start() - 1 : match.start()] if match.start() else ""
        if before and before in _MONEY_BEFORE:
            return None
        # 值级冲突：这个值已作为别的槽位存过（如复述已给的手机号）→ 不填
        if any(str(v) == token for k, v in collected.items() if k != pending_slot):
            return None
        return token
    return None
