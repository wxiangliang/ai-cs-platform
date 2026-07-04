"""语言包汇总（Stage 19）。

每个语言一个模块导出 `TABLE: dict[str, str]`（key → 文案模板）。
`zh` 是源语言，逐字对齐现有硬编码文案（保证 i18n 收口零回归）；
其它语言按需补齐，缺的 key 由 i18n.t() 回退到默认语言。
"""

from app.locales.en import TABLE as EN
from app.locales.zh import TABLE as ZH

# locale → {key: template}
LOCALES: dict[str, dict[str, str]] = {
    "zh": ZH,
    "en": EN,
}
