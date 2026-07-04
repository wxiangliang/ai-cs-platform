"""LLM prompt 组装（Stage 04-01）。

原则：
- 意图清单从 app/chat/intent/catalog.py 程序化生成（单一事实来源），禁止手抄；
- 护栏红线（浓缩自 docs/chat/skills_design/guardrails.md）进所有生成类 system prompt；
- 对话历史注入做轮数与长度截断，避免 token 失控。
"""

from app.chat.intent.catalog import BOUNDARY_RULES, INTENT_DESCRIPTIONS
from app.chat.llm.prompt_guard import wrap_user_input
from app.core.config import settings

# 全局护栏浓缩版（完整版见 docs/chat/skills_design/guardrails.md；
# Stage 05 Skill Loader 落地后改为从 md 文件加载）
GUARDRAILS_CONDENSED = """必须遵守的红线：
1. 价格/折扣/退款结果/时效必须来自系统给出的事实，没有依据就说「需要核实」，禁止编造。
2. 不假装真人，也不刻意强调 AI 身份；用户问业务问题时直接答业务。
3. 每轮先回应当前消息；最多追问一个问题；不使用表情符号。
4. 不承诺赔偿、补发、免费保留等未经人工确认的处理结果。"""


def build_intent_second_opinion_prompt(text: str, top_k: list[dict]) -> tuple[str, str]:
    """SetFit 低置信难例的 LLM 二判 prompt。

    只在候选（top_k）+ 全量目录中选择，输出必须是意图码本身，便于严格校验。
    """
    catalog = "\n".join(f"- {label}：{desc}" for label, desc in INTENT_DESCRIPTIONS.items())
    candidates = "、".join(t["label"] for t in top_k) if top_k else "无"
    system = (
        "你是客服意图分类器。从下面的意图清单中选择最匹配用户消息的一个意图码，"
        "只输出意图码本身（如 LOGISTICS.TRACK），不要输出其他任何内容。\n\n"
        f"意图清单：\n{catalog}\n\n{BOUNDARY_RULES}"
    )
    # 用户原文经防注入包裹（Stage 14）：数据而非指令
    user = f"用户消息：\n{wrap_user_input(text)}\n（初步模型的候选参考：{candidates}，可以不采纳）"
    return system, user


def build_slot_extraction_prompt(
    text: str, intent: str, slot_names: list[str]
) -> tuple[str, str]:
    """LLM 槽位抽取兜底 prompt：只抽取声明的槽位，输出 JSON。"""
    system = (
        "你是客服对话的槽位抽取器。从用户消息中抽取指定槽位的值，"
        '只输出一个 JSON 对象（如 {"order_id": "A123"}），'
        "消息中没有提到的槽位不要输出，禁止编造。"
    )
    user = f"意图：{intent}\n需要抽取的槽位：{', '.join(slot_names)}\n用户消息：\n{wrap_user_input(text)}"
    return system, user


def build_reply_polish_prompt(
    draft: str,
    user_message: str,
    history: list[tuple[str, str]],
    session_summary: str = "",
    long_term_facts: list[str] | None = None,
    soften: bool = False,
) -> tuple[str, str]:
    """回复润色 prompt：把模板底稿改写为自然话术，事实不许改动。

    history: [(role, content)] 最近对话，注入前按 LLM_HISTORY_MAX_TURNS 截断；
    session_summary / long_term_facts: 记忆上下文（Stage 10），
    仅用于语气与称呼的贴合，禁止把记忆内容当作本轮新事实写进回复；
    soften: 用户情绪负面（Stage 14 护栏标记）→ 先安抚再答复。
    """
    soften_note = (
        "\n用户当前情绪不佳：先用一句话表达理解与歉意，再给出答复，语气务必温和。"
        if soften
        else ""
    )
    system = (
        "你是电商客服。把「回复底稿」改写成自然、简洁、有温度的客服话术。\n"
        "要求：底稿中的事实（数字、金额、商品名、订单号、来源引用）必须原样保留，"
        f"不新增底稿之外的信息；长度不超过底稿的 1.5 倍。{soften_note}\n"
        "回复语言与用户当前消息保持一致：用户用什么语言，就用什么语言回复（Stage 19）。\n\n"
        + GUARDRAILS_CONDENSED
    )
    trimmed = history[-settings.LLM_HISTORY_MAX_TURNS * 2 :]
    history_text = "\n".join(f"{'用户' if r == 'user' else '客服'}：{c[:200]}" for r, c in trimmed)
    memory_lines = []
    if session_summary:
        memory_lines.append(f"本次会话此前概要：{session_summary}")
    if long_term_facts:
        memory_lines.append("已知用户信息：" + "；".join(long_term_facts[:5]))
    memory_text = "\n".join(memory_lines)
    user = (
        (f"{memory_text}\n\n" if memory_text else "")
        + (f"最近对话：\n{history_text}\n\n" if history_text else "")
        + f"用户本轮消息：\n{wrap_user_input(user_message)}\n"
        + f"回复底稿：{draft}\n\n请输出改写后的回复（只输出回复正文）。"
    )
    return system, user
