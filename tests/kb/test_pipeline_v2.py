"""检索管道 v2 单元测试：query 归一化 / 加权 RRF / rerank 开关。"""

from app.kb.backends.base import rrf_fuse
from app.kb.query_normalize import normalize_query
from app.kb.rerank import rerank_hits
from app.kb.types import Hit


def test_normalize_traditional_to_simplified():
    nq = normalize_query("退貨運費誰承擔")
    assert nq.text == "退货运费谁承担"


def test_normalize_synonym_expansion():
    nq = normalize_query("运费谁出")
    assert "邮费" in nq.expanded_terms and "快递费" in nq.expanded_terms
    # 反向：口语词 → 标准词
    nq2 = normalize_query("邮费怎么算")
    assert "运费" in nq2.expanded_terms


def test_normalize_model_code_and_query_type():
    nq = normalize_query("KFR-35GW 保修多久")
    assert "KFR-35GW" in nq.model_codes and nq.query_type == "precise"
    assert normalize_query("买了不喜欢可以退吗").query_type == "semantic"
    # 长数字单号也算精确查询
    assert normalize_query("订单 202406180001 到哪了").query_type == "precise"


def _hit(hid: str, backend: str = "milvus") -> Hit:
    return Hit(id=hid, score=0.5, source_backend=backend)


def test_weighted_rrf_keyword_boost():
    """precise 权重下，关键词路的头名应压过向量路头名。"""
    vector = [_hit("v1"), _hit("both")]
    keyword = [_hit("k1", "pg_keyword"), _hit("both", "pg_keyword")]
    fused = rrf_fuse([vector, keyword], top_k=4, weights=[0.8, 1.5])
    # both 双路命中仍第一；k1 因关键词权重应排在 v1 前
    ids = [h.id for h in fused]
    assert ids[0] == "both" and ids.index("k1") < ids.index("v1")


async def test_rerank_off_is_passthrough(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "RERANKER_PROVIDER", "off")
    hits = [_hit("a"), _hit("b"), _hit("c")]
    out = await rerank_hits("q", hits, top_k=2)
    assert [h.id for h in out] == ["a", "b"]  # RRF 序截断
