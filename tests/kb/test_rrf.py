"""RRF 融合函数单元测试。"""

from app.kb.backends.base import rrf_fuse
from app.kb.types import Hit


def _hit(hid: str, score: float, backend: str = "milvus", content: str | None = None) -> Hit:
    return Hit(id=hid, score=score, source_backend=backend, content=content)


def test_rrf_both_lists_boost():
    vec = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    kw = [_hit("b", 0.0, "pg_keyword", content="b内容"), _hit("d", 0.0, "pg_keyword")]
    fused = rrf_fuse([vec, kw], top_k=4)
    # b 同时出现在两路，应排第一
    assert fused[0].id == "b"
    # 保留了带内容的那份命中
    assert fused[0].content == "b内容"
    assert {h.id for h in fused} == {"a", "b", "c", "d"}


def test_rrf_empty():
    assert rrf_fuse([[], []]) == []


def test_rrf_top_k_truncates():
    vec = [_hit(str(i), 1.0 - i * 0.1) for i in range(10)]
    fused = rrf_fuse([vec], top_k=3)
    assert len(fused) == 3
