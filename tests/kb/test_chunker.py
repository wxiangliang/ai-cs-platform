"""分块器单元测试。"""

from app.kb.chunker import clean_text, split_chunks


def test_clean_text_strips_html_and_whitespace():
    raw = "<p>退货政策  说明</p>\r\n\r\n第一条：  签收后 7 天内可退。"
    cleaned = clean_text(raw)
    assert "<p>" not in cleaned
    assert "退货政策 说明" in cleaned
    assert "签收后 7 天内可退" in cleaned


def test_split_chunks_respects_size():
    text = "\n\n".join(f"第{i}段。" + "内容" * 60 for i in range(6))
    chunks = split_chunks(text, chunk_size=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_split_chunks_overlap_keeps_context():
    # 单段超长文本硬切时，相邻块应有重叠
    text = "甲" * 450
    chunks = split_chunks(text, chunk_size=200, overlap=50)
    assert len(chunks) >= 2
    # 第二块开头应与第一块结尾重叠
    assert chunks[1][:10] == chunks[0][-50:][:10]


def test_split_chunks_empty():
    assert split_chunks("") == []
