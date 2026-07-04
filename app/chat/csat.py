"""CSAT 评分解析（Stage 15）。

仅在会话被标记 csat_pending（人工归还/会话关闭后的询问轮）时调用；
非评分回复返回 None（照常进主链路，询问一次性失效——不打扰正常提问）。
"""

import re

# 「4分」「4 分」「打4」或裸数字 1-5
_SCORE_RE = re.compile(r"^(?:打)?([1-5])\s*分?$")

# 口语 → 分值（顺序即优先级，先长后短防「不满意」命中「满意」）
_WORD_SCORES: tuple[tuple[str, int], ...] = (
    ("非常不满意", 1),
    ("很不满意", 1),
    ("不满意", 2),
    ("非常满意", 5),
    ("很满意", 5),
    ("满意", 5),
    ("很差", 1),
    ("太差", 1),
    ("一般", 3),
    ("还行", 4),
    ("不错", 4),
    ("很好", 5),
    ("好评", 5),
)


def parse_csat_score(text: str) -> int | None:
    """解析评分：1-5 数字或口语词完全命中才算（防误吞正常业务消息）。"""
    stripped = text.strip().rstrip("。！!~，,")
    m = _SCORE_RE.match(stripped)
    if m:
        return int(m.group(1))
    for word, score in _WORD_SCORES:
        if stripped == word:
            return score
    return None
