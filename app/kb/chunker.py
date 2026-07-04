"""文档清洗与分块器。

两套切分：
1. chunk_blocks（v2，结构感知）：面对解析层的 Block IR，按章节树切分，
   每个 chunk 注入标题路径（防语义悬空），表格独立成块 + 大表分片重复表头，
   图片用 caption+邻近正文合块（见 stage-06-02 需求第 3 节）。
2. split_chunks（v1，纯文本兜底）：按段落/句子边界贪心合并，无结构可用时使用。

独立纯函数，便于单元测试；参数走 settings。
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.kb.parsing.base import Block

# 段落分隔：markdown 标题或连续空行
_PARA_SPLIT_RE = re.compile(r"\n\s*\n|\n(?=#{1,6}\s)")
# 句子边界（中文句号/问叹号/分号 + 英文句点）
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])|(?<=\.\s)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(raw: str) -> str:
    """清洗原文：去 HTML 标签、统一换行、压缩多余空白。"""
    text = _HTML_TAG_RE.sub(" ", raw or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 行内多空格压缩，但保留换行结构（分段依据）
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def split_chunks(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """把清洗后的文本切成分块列表。"""
    size = chunk_size or settings.KB_CHUNK_SIZE
    ov = overlap if overlap is not None else settings.KB_CHUNK_OVERLAP

    paragraphs = [p.strip() for p in _PARA_SPLIT_RE.split(text) if p.strip()]
    # 超长段落先按句子拆开
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= size:
            pieces.append(para)
        else:
            pieces.extend(s.strip() for s in _SENT_SPLIT_RE.split(para) if s.strip())

    # 贪心合并到目标大小
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + len(piece) + 1 > size:
            chunks.append(buf)
            # 重叠：带上上一块的尾部，保证跨块上下文连续
            buf = (buf[-ov:] + "\n" if ov else "") + piece
        else:
            buf = f"{buf}\n{piece}" if buf else piece
        # 单条 piece 仍超长时硬切
        while len(buf) > size:
            chunks.append(buf[:size])
            buf = (buf[size - ov :] if ov else buf[size:]).strip()
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


# ---------------------------------------------------------------------------
# v2 结构感知切分（Block IR → Chunk）
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    """切分结果：文本 + 结构元数据（heading_path 等入 kb_chunk.metadata_json）。"""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _with_heading(path: list[str], body: str) -> str:
    """把标题路径注入 chunk 文本头部：[父标题 > 子标题]\\n正文。"""
    if not path:
        return body
    return f"[{' > '.join(path)}]\n{body}"


def _split_table_by_rows(table_md: str, size: int) -> list[str]:
    """大表按行组切分，每个分片重复表头（前两行：表头 + 分隔行）。"""
    lines = table_md.split("\n")
    if len(lines) <= 2 or len(table_md) <= size:
        return [table_md]
    header, body_rows = lines[:2], lines[2:]
    pieces: list[str] = []
    buf: list[str] = []
    budget = max(size - len("\n".join(header)), 100)
    for row in body_rows:
        if buf and len("\n".join(buf)) + len(row) + 1 > budget:
            pieces.append("\n".join(header + buf))
            buf = []
        buf.append(row)
    if buf:
        pieces.append("\n".join(header + buf))
    return pieces


def chunk_blocks(
    blocks: list[Block],
    chunk_size: int | None = None,
    overlap: int | None = None,
    min_chars: int | None = None,
) -> list[Chunk]:
    """结构感知切分：Block IR → Chunk 列表。

    规则（stage-06-02 第 3 节）：
    - 章节树 + 标题路径注入；段落只在同一章节内贪心合并，永不跨章节；
    - 表格独立成块（带引导句），超限按行组分片并重复表头；
    - 图片块：caption 文本 + 邻近段落合块；无可检索文本的裸图丢弃；
    - 代码块整块保留；碎片（< min_chars）并入相邻块。
    """
    size = chunk_size or settings.KB_CHUNK_SIZE
    ov = overlap if overlap is not None else settings.KB_CHUNK_OVERLAP
    tiny = min_chars if min_chars is not None else settings.KB_MIN_CHUNK_CHARS

    chunks: list[Chunk] = []
    heading_stack: list[tuple[int, str]] = []  # (level, title)
    para_buf: list[str] = []  # 当前章节内待合并段落
    buf_page: int | None = None

    def heading_path() -> list[str]:
        return [t for _, t in heading_stack]

    def flush_paras() -> None:
        """把缓冲段落合并输出为一个或多个 chunk（句子边界二次切分）。"""
        nonlocal buf_page
        if not para_buf:
            return
        body = "\n".join(para_buf)
        para_buf.clear()
        for piece in split_chunks(body, chunk_size=size, overlap=ov):
            meta: dict[str, Any] = {"heading_path": heading_path(), "block_type": "paragraph"}
            if buf_page is not None:
                meta["page"] = buf_page
            chunks.append(Chunk(text=_with_heading(heading_path(), piece), metadata=meta))
        buf_page = None

    last_paragraph = ""  # 表格/图片的引导句来源
    for i, block in enumerate(blocks):
        if block.type == "heading":
            flush_paras()
            level = max(block.level, 1)
            # 弹出更深/同级的标题，维持祖先链
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, block.text))
            last_paragraph = ""
        elif block.type == "table":
            flush_paras()
            intro = (block.meta.get("caption") or last_paragraph or "")[:100]
            for piece in _split_table_by_rows(block.text, size):
                body = f"{intro}\n{piece}" if intro else piece
                chunks.append(
                    Chunk(
                        text=_with_heading(heading_path(), body),
                        metadata={
                            "heading_path": heading_path(),
                            "block_type": "table",
                            **({"page": block.page} if block.page else {}),
                        },
                    )
                )
        elif block.type == "image":
            # 图片：caption + 后一个段落作为文本代理；无文本线索的裸图丢弃
            neighbor = ""
            for nxt in blocks[i + 1 : i + 2]:
                if nxt.type == "paragraph":
                    neighbor = nxt.text[:200]
            text = " ".join(t for t in [block.text, neighbor] if t).strip()
            if text:
                chunks.append(
                    Chunk(
                        text=_with_heading(heading_path(), f"（图）{text}"),
                        metadata={
                            "heading_path": heading_path(),
                            "block_type": "image",
                            "img_path": block.meta.get("img_path"),
                            **({"page": block.page} if block.page else {}),
                        },
                    )
                )
        elif block.type == "code":
            flush_paras()
            chunks.append(
                Chunk(
                    text=_with_heading(heading_path(), block.text),
                    metadata={"heading_path": heading_path(), "block_type": "code"},
                )
            )
        else:  # paragraph
            para_buf.append(block.text)
            if block.page is not None and buf_page is None:
                buf_page = block.page
            last_paragraph = block.text
            # 缓冲超限即输出，保证段落合并不超 chunk 大小
            if sum(len(p) for p in para_buf) >= size:
                flush_paras()

    flush_paras()

    # 碎片合并：过短的 chunk 并入前一个（同章节优先，简单向前合并）
    merged: list[Chunk] = []
    for chunk in chunks:
        body_len = len(chunk.text)
        if merged and body_len < tiny and merged[-1].metadata.get("block_type") == chunk.metadata.get("block_type"):
            merged[-1].text += "\n" + chunk.text
        else:
            merged.append(chunk)
    return merged
