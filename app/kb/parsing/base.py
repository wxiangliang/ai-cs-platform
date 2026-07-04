"""解析层公共类型：Block 中间表示（IR）与 DocumentParser 协议。

所有解析器输出统一的 Block 序列，结构感知切分器只面对 IR：
- heading：文档标题，level 1-6，用于构建章节树与标题路径注入；
- paragraph：正文段落（公式/OCR 文本也归入）；
- table：Markdown 表格文本；
- image：图片的文本代理（caption/脚注），img_path 存 meta（预留多模态升级）；
- code：代码块，整块保留不切。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Block:
    """解析后的文档块（统一中间表示）。"""

    type: str  # heading / paragraph / table / image / code
    text: str
    level: int = 0  # heading 层级（1-6），其余类型为 0
    page: int | None = None  # 页码（PDF 来源时）
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    """解析结果：块序列 + 使用的解析器名（审计）。"""

    blocks: list[Block]
    parser: str

    def to_markdown(self) -> str:
        """把块序列还原为 Markdown 全文（存 kb_document.raw_content，供重建分块）。"""
        lines: list[str] = []
        for b in self.blocks:
            if b.type == "heading":
                lines.append("#" * max(b.level, 1) + " " + b.text)
            elif b.type == "code":
                lines.append(f"```\n{b.text}\n```")
            else:
                lines.append(b.text)
        return "\n\n".join(lines)


class DocumentParser(Protocol):
    """文档解析器协议：输入文件字节流，输出 Block IR。

    不可用（未配置/未安装/调用失败）时抛 ParserUnavailable，
    由 router 降级到链上的下一级解析器。
    """

    name: str

    async def parse(self, filename: str, content: bytes) -> ParsedDocument: ...


class ParserUnavailable(Exception):
    """解析器不可用（未配置/未安装/服务不可达），触发降级。"""


# ---------------------------------------------------------------------------
# Markdown → Block 结构解析（多个解析器共用：md 文件、Docling/MinerU 的 md 输出）
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def markdown_to_blocks(md: str, parser: str = "markdown") -> list[Block]:
    """把 Markdown 文本解析为 Block 序列（标题/段落/表格/代码块）。"""
    blocks: list[Block] = []
    lines = (md or "").replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)
    para_buf: list[str] = []

    def flush_para() -> None:
        if para_buf:
            text = " ".join(p.strip() for p in para_buf).strip()
            if text:
                blocks.append(Block(type="paragraph", text=text))
            para_buf.clear()

    while i < n:
        line = lines[i]
        # 代码块：整块收集
        if line.strip().startswith("```"):
            flush_para()
            code_lines: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            if code_lines:
                blocks.append(Block(type="code", text="\n".join(code_lines)))
            continue
        # 标题
        m = _HEADING_RE.match(line)
        if m:
            flush_para()
            blocks.append(Block(type="heading", text=m.group(2).strip(), level=len(m.group(1))))
            i += 1
            continue
        # 表格：连续的 | 行整块收集
        if _TABLE_ROW_RE.match(line):
            flush_para()
            table_lines: list[str] = []
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                table_lines.append(lines[i].strip())
                i += 1
            blocks.append(Block(type="table", text="\n".join(table_lines)))
            continue
        # 空行分段
        if not line.strip():
            flush_para()
            i += 1
            continue
        para_buf.append(line)
        i += 1

    flush_para()
    return blocks
