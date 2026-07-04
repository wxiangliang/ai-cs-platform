"""检索查询归一化（Stage 06-04 检索管道 v2）。

做三件事（对应参考方案的 Query Normalization 层）：
1. 繁简转换（zhconv，繁体用户查询命中简体知识库）；
2. 同义词扩展（内置基础词表 + 可扩展，扩展词只加入关键词召回路，不改原句向量）；
3. 型号/编号识别（大写字母数字混合 SKU/型号），并据此判定查询类型：
   precise（含型号/单号/数字，关键词路权重大）/ semantic（口语语义问题，向量路权重大）。

拼写纠错暂不做（需要专门模型，收益/成本比低，文档记录为后续项）。
"""

import re
from dataclasses import dataclass, field

from zhconv import convert

# 同义词扩展表（客服领域基础词表；租户级定制词表后续从配置/库加载）
_SYNONYMS: dict[str, list[str]] = {
    "运费": ["邮费", "快递费"],
    "包邮": ["免运费"],
    "退货": ["退回", "寄回"],
    "退款": ["退钱"],
    "发票": ["开票"],
    "保修": ["质保", "维修保障"],
    "快递": ["物流", "包裹"],
    "优惠券": ["折扣券", "券"],
}
# 反向索引（同义词 → 标准词）
_REVERSE_SYNONYMS: dict[str, str] = {
    alias: word for word, aliases in _SYNONYMS.items() for alias in aliases
}

# 型号/编号：大写字母开头的字母数字混合（KFR-35GW、X1、SKU1001），或长数字单号
_MODEL_CODE_RE = re.compile(r"[A-Za-z]{1,6}[-_]?\d{1,10}[A-Za-z0-9-]*|\d{8,}")


@dataclass
class NormalizedQuery:
    """归一化结果。"""

    text: str  # 归一化后的查询（繁→简）
    expanded_terms: list[str] = field(default_factory=list)  # 同义词扩展词（关键词路专用）
    model_codes: list[str] = field(default_factory=list)  # 识别出的型号/编号
    query_type: str = "semantic"  # precise / semantic


def normalize_query(raw: str) -> NormalizedQuery:
    """归一化查询：繁简转换 + 同义词扩展 + 型号识别 + 类型判定。"""
    text = convert((raw or "").strip(), "zh-cn")

    expanded: list[str] = []
    for word, aliases in _SYNONYMS.items():
        if word in text:
            expanded.extend(aliases)
    for alias, word in _REVERSE_SYNONYMS.items():
        if alias in text and word not in text:
            expanded.append(word)

    codes = [m.group(0) for m in _MODEL_CODE_RE.finditer(text)]
    # 类型判定：含型号/编号 → precise（关键词路权重大：型号/单号向量不可靠）
    query_type = "precise" if codes else "semantic"

    return NormalizedQuery(
        text=text,
        expanded_terms=list(dict.fromkeys(expanded)),
        model_codes=codes,
        query_type=query_type,
    )
