"""格式路由：按扩展名选择解析器链，逐级降级。

链上前一级抛 ParserUnavailable 自动尝试下一级；全部失败抛 DocumentParseError
（调用方明确报错，不入库半成品）。
"""

from pathlib import Path

from app.core.logging import get_logger
from app.kb.parsing.base import DocumentParser, ParsedDocument, ParserUnavailable
from app.kb.parsing.builtin_parsers import DocxParser, MarkdownParser, PypdfParser, XlsxParser
from app.kb.parsing.docling_parser import DoclingParser
from app.kb.parsing.mineru_parser import MineruParser

logger = get_logger(__name__)


class DocumentParseError(Exception):
    """所有解析器都失败。"""


# 每种扩展名一条解析器链（优先级从高到低，见 stage-06-02 需求第 1 节）
_CHAINS: dict[str, list[DocumentParser]] = {
    ".pdf": [MineruParser(), DoclingParser(), PypdfParser()],
    ".docx": [DoclingParser(), DocxParser()],
    ".xlsx": [XlsxParser()],
    ".xls": [XlsxParser()],
    ".md": [MarkdownParser()],
    ".markdown": [MarkdownParser()],
    ".txt": [MarkdownParser()],
}

SUPPORTED_EXTENSIONS = sorted(_CHAINS.keys())


async def parse_document(filename: str, content: bytes) -> ParsedDocument:
    """按扩展名路由解析器链，返回 Block IR。"""
    ext = Path(filename).suffix.lower()
    chain = _CHAINS.get(ext)
    if chain is None:
        raise DocumentParseError(f"不支持的文件格式 {ext}，支持：{', '.join(SUPPORTED_EXTENSIONS)}")

    errors: list[str] = []
    for parser in chain:
        try:
            doc = await parser.parse(filename, content)
            logger.info("document parsed: file=%s parser=%s blocks=%d", filename, parser.name, len(doc.blocks))
            return doc
        except ParserUnavailable as exc:
            # 该级不可用，降级下一级
            logger.warning("parser %s unavailable for %s: %s", parser.name, filename, exc)
            errors.append(f"{parser.name}: {exc}")

    raise DocumentParseError(f"文件 {filename} 解析失败（{'; '.join(errors)}）")
