"""文档解析（Block IR）与结构感知切分单元测试。"""

import io

from app.kb.chunker import chunk_blocks
from app.kb.parsing.base import Block, markdown_to_blocks
from app.kb.parsing.builtin_parsers import XlsxParser
from app.kb.parsing.mineru_parser import content_list_to_blocks

MD = """# 退换货政策

总则说明。

## 退货运费

因质量问题退货运费由商家承担。

| 场景 | 承担方 |
|---|---|
| 质量问题 | 商家 |
| 无理由 | 买家 |

```
code sample
```
"""


def test_markdown_to_blocks_structure():
    blocks = markdown_to_blocks(MD)
    types = [(b.type, b.level) for b in blocks]
    assert ("heading", 1) in types and ("heading", 2) in types
    assert any(b.type == "table" and "承担方" in b.text for b in blocks)
    assert any(b.type == "code" and "code sample" in b.text for b in blocks)
    assert any(b.type == "paragraph" and "总则" in b.text for b in blocks)


def test_chunk_blocks_heading_path_injection():
    blocks = markdown_to_blocks(MD)
    chunks = chunk_blocks(blocks, chunk_size=200, min_chars=5)
    # 「退货运费」章节下的段落必须带完整标题路径
    fee_chunks = [c for c in chunks if "商家承担" in c.text and c.metadata["block_type"] == "paragraph"]
    assert fee_chunks, "应有退货运费段落 chunk"
    assert fee_chunks[0].text.startswith("[退换货政策 > 退货运费]")
    assert fee_chunks[0].metadata["heading_path"] == ["退换货政策", "退货运费"]


def test_chunk_blocks_table_standalone_with_intro():
    blocks = markdown_to_blocks(MD)
    chunks = chunk_blocks(blocks, chunk_size=200, min_chars=5)
    table_chunks = [c for c in chunks if c.metadata["block_type"] == "table"]
    assert len(table_chunks) == 1
    # 表格独立成块 + 引导句（表格前段落）注入 + 标题路径
    assert "| 场景 | 承担方 |" in table_chunks[0].text
    assert "商家承担" in table_chunks[0].text  # 引导句
    assert table_chunks[0].text.startswith("[退换货政策 > 退货运费]")


def test_big_table_split_repeats_header():
    header = "| 型号 | 价格 |\n|---|---|"
    rows = "\n".join(f"| 型号{i} | {i}00 元 |" for i in range(60))
    blocks = [Block(type="heading", text="价格表", level=1), Block(type="table", text=f"{header}\n{rows}")]
    chunks = chunk_blocks(blocks, chunk_size=400, min_chars=5)
    table_chunks = [c for c in chunks if c.metadata["block_type"] == "table"]
    assert len(table_chunks) > 1, "大表应被分片"
    # 每个分片都必须重复表头
    for c in table_chunks:
        assert "| 型号 | 价格 |" in c.text


def test_image_block_uses_caption_and_neighbor():
    blocks = [
        Block(type="heading", text="安装指南", level=1),
        Block(type="image", text="图1 安装示意", meta={"img_path": "img/1.jpg"}),
        Block(type="paragraph", text="如上图所示，先固定支架再挂机。"),
    ]
    chunks = chunk_blocks(blocks, chunk_size=200, min_chars=5)
    img_chunks = [c for c in chunks if c.metadata["block_type"] == "image"]
    assert img_chunks and "图1 安装示意" in img_chunks[0].text
    assert "固定支架" in img_chunks[0].text  # 邻近正文并入
    assert img_chunks[0].metadata["img_path"] == "img/1.jpg"
    # 无文本线索的裸图丢弃
    bare = chunk_blocks([Block(type="image", text="", meta={})], min_chars=5)
    assert not [c for c in bare if c.metadata.get("block_type") == "image"]


def test_mineru_content_list_mapping():
    items = [
        {"type": "text", "text": "产品手册", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "正文段落。", "page_idx": 0},
        {"type": "table", "table_body": "<table><tr><td>A</td><td>B</td></tr></table>",
         "table_caption": ["参数表"], "page_idx": 1},
        {"type": "image", "image_caption": ["图2 结构图"], "img_path": "images/2.jpg", "page_idx": 1},
    ]
    blocks = content_list_to_blocks(items)
    assert blocks[0].type == "heading" and blocks[0].level == 1 and blocks[0].page == 1
    assert blocks[1].type == "paragraph"
    assert blocks[2].type == "table" and "| A | B |" in blocks[2].text
    assert blocks[3].type == "image" and blocks[3].meta["img_path"] == "images/2.jpg"


async def test_xlsx_parser_sheet_to_table():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "价格表"
    ws.append(["型号", "价格"])
    ws.append(["X1", "1999"])
    buf = io.BytesIO()
    wb.save(buf)

    parsed = await XlsxParser().parse("test.xlsx", buf.getvalue())
    assert parsed.blocks[0].type == "heading" and parsed.blocks[0].text == "价格表"
    assert parsed.blocks[1].type == "table" and "| X1 | 1999 |" in parsed.blocks[1].text
    # 切分后 sheet 名成为标题路径
    chunks = chunk_blocks(parsed.blocks, min_chars=5)
    assert chunks[0].text.startswith("[价格表]")
