"""MinerU 解析器（HTTP 接入）。

调用自建 MinerU API 服务（POST {MINERU_API_URL}/file_parse，multipart 上传），
优先消费 content_list（版面块结构），仅有 md_content 时降级 Markdown 结构解析。

content_list → Block 映射（MinerU 标准 schema）：
- text_level >= 1        → heading
- type=text/equation     → paragraph
- type=table             → table（table_body HTML 转 Markdown）
- type=image             → image（caption+footnote 作为文本代理，img_path 存 meta）
"""

import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.kb.parsing.base import Block, ParsedDocument, ParserUnavailable, markdown_to_blocks

logger = get_logger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _html_table_to_markdown(html: str) -> str:
    """HTML 表格转 Markdown（轻量实现：按 tr/td 提取）。"""
    rows: list[list[str]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html or "", flags=re.S | re.I):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S | re.I)
        rows.append([_TAG_RE.sub(" ", c).strip() for c in cells])
    rows = [r for r in rows if any(r)]
    if not rows:
        return _TAG_RE.sub(" ", html or "").strip()
    width = max(len(r) for r in rows)
    md = ["| " + " | ".join(r + [""] * (width - len(r))) + " |" for r in rows]
    md.insert(1, "|" + "---|" * width)
    return "\n".join(md)


def content_list_to_blocks(items: list[dict[str, Any]]) -> list[Block]:
    """MinerU content_list → Block IR（独立函数便于单元测试）。"""
    blocks: list[Block] = []
    for item in items or []:
        itype = item.get("type", "text")
        page = item.get("page_idx")
        page_no = (page + 1) if isinstance(page, int) else None
        level = item.get("text_level") or 0

        if itype == "text" and level >= 1:
            text = (item.get("text") or "").strip()
            if text:
                blocks.append(Block(type="heading", text=text, level=min(int(level), 6), page=page_no))
        elif itype in ("text", "equation"):
            text = (item.get("text") or "").strip()
            if text:
                blocks.append(Block(type="paragraph", text=text, page=page_no))
        elif itype == "table":
            body = item.get("table_body") or ""
            caption = " ".join(item.get("table_caption") or [])
            md = _html_table_to_markdown(body)
            if md:
                blocks.append(
                    Block(type="table", text=md, page=page_no, meta={"caption": caption})
                )
        elif itype == "image":
            caption = " ".join(item.get("image_caption") or [])
            footnote = " ".join(item.get("image_footnote") or [])
            text = f"{caption} {footnote}".strip()
            blocks.append(
                Block(
                    type="image",
                    text=text,
                    page=page_no,
                    meta={"img_path": item.get("img_path")},
                )
            )
    return blocks


class MineruParser:
    """MinerU HTTP 解析器。未配置 MINERU_API_URL 时不可用（降级下一级）。"""

    name = "mineru_http"

    async def parse(self, filename: str, content: bytes) -> ParsedDocument:
        if not settings.MINERU_API_URL:
            raise ParserUnavailable("MINERU_API_URL 未配置")
        url = settings.MINERU_API_URL.rstrip("/") + "/file_parse"
        try:
            async with httpx.AsyncClient(timeout=settings.MINERU_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    files={"file": (filename, content)},
                    data={"return_content_list": "true", "return_md": "true"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:  # noqa: BLE001 - 服务不可达/超时 → 降级下一级
            raise ParserUnavailable(f"MinerU 调用失败: {type(exc).__name__}") from exc

        # 兼容不同版本的响应包裹（results 字典 / 平铺字段）
        data = payload
        if isinstance(payload.get("results"), dict) and payload["results"]:
            data = next(iter(payload["results"].values()))

        items = data.get("content_list")
        if items:
            blocks = content_list_to_blocks(items)
        else:
            md = data.get("md_content") or ""
            if not md:
                raise ParserUnavailable("MinerU 响应无 content_list 也无 md_content")
            blocks = markdown_to_blocks(md, parser=self.name)
        if not blocks:
            raise ParserUnavailable("MinerU 未解析出内容")
        return ParsedDocument(blocks=blocks, parser=self.name)
