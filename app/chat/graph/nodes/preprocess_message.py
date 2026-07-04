"""节点：preprocess_message —— 清洗与归一化用户消息。

第一版做：去首尾空白、压缩连续空白、全角转半角（数字与字母），便于后续规则匹配。
"""

from typing import Any

from app.chat.graph.state import GraphState

# 全角 → 半角转换表（针对 ASCII 可见字符与全角空格）
_FULL2HALF = {0x3000: 0x20, **{c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}}


async def preprocess_message(state: GraphState) -> dict[str, Any]:
    """归一化文本。"""
    raw = state.get("message", "") or ""
    # 全角转半角，避免“１２３”这类输入匹配不到正则
    normalized = raw.translate(_FULL2HALF)
    # 压缩空白并去首尾
    normalized = " ".join(normalized.split()).strip()

    return {
        "normalized_text": normalized,
        "graph_trace": ["preprocess_message"],
    }
