"""hash embedding 客户端单元测试。"""

import math

from app.kb.embedding import HashEmbeddingClient


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


async def test_deterministic_and_dim():
    client = HashEmbeddingClient(dim=128)
    v1, v2 = await client.embed(["退货运费谁承担", "退货运费谁承担"])
    assert v1 == v2
    assert len(v1) == 128
    # L2 归一
    assert abs(math.sqrt(sum(x * x for x in v1)) - 1.0) < 1e-6


async def test_similar_text_scores_higher():
    client = HashEmbeddingClient(dim=256)
    base, similar, unrelated = await client.embed(
        ["退货运费由谁承担", "退货的运费谁来承担", "今天天气怎么样"]
    )
    assert _cos(base, similar) > _cos(base, unrelated)
