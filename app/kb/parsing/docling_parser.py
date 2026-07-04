"""Docling 解析器（本地库）。

v1 用 Markdown 中转：DocumentConverter → export_to_markdown → 内置 Markdown 结构解析。
表格/标题层级在 Docling 的 md 输出中保留完好；需要页码/坐标时再升级为遍历原生节点。

Docling 首次运行会下载版面模型（可能较慢/离线失败），转换是 CPU 密集同步调用，
放线程池执行；任何失败都转 ParserUnavailable 触发降级。
"""

import asyncio
import tempfile
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.kb.parsing.base import ParsedDocument, ParserUnavailable, markdown_to_blocks

logger = get_logger(__name__)


class DoclingParser:
    """Docling 本地解析器（支持 pdf / docx / xlsx 等）。"""

    name = "docling"

    async def parse(self, filename: str, content: bytes) -> ParsedDocument:
        if not settings.DOCLING_ENABLED:
            raise ParserUnavailable("DOCLING_ENABLED=false")
        try:
            md = await asyncio.to_thread(self._convert_sync, filename, content)
        except ParserUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - 模型下载失败/转换异常 → 降级
            raise ParserUnavailable(f"Docling 转换失败: {type(exc).__name__}") from exc

        blocks = markdown_to_blocks(md, parser=self.name)
        if not blocks:
            raise ParserUnavailable("Docling 未解析出内容")
        return ParsedDocument(blocks=blocks, parser=self.name)

    @staticmethod
    def _convert_sync(filename: str, content: bytes) -> str:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise ParserUnavailable("docling 未安装") from exc

        suffix = Path(filename).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(content)
            tmp.flush()
            converter = DocumentConverter()
            result = converter.convert(tmp.name)
            return result.document.export_to_markdown()
