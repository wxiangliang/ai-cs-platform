"""示例向量交叉验证测试（Stage 26 遗留 3；功能默认关闭=零回归）。

不依赖真实 SetFit 权重：假编码器 + 临时目录里手工构造的小索引。
"""

import json

import numpy as np
import pytest

from app.chat.intent import example_knn
from app.chat.intent.hybrid_classifier import hybrid_intent_classifier
from app.chat.intent.types import DecisionSource
from app.core.config import settings


class _FakeEncoder:
    """假 SetFit 单例：只提供 available 与 encode。"""

    available = True

    def __init__(self, vec):
        self._vec = np.asarray(vec, dtype=np.float32)

    def encode(self, text: str):
        return self._vec


def _make_index(tmp_path, rows: list[tuple[str, list[float]]]):
    """写一个手工小索引：rows = [(label, 归一化向量), ...]。"""
    matrix = np.asarray([v for _, v in rows], dtype=np.float32)
    np.save(tmp_path / "embeddings.npy", matrix)
    (tmp_path / "labels.json").write_text(
        json.dumps([lb for lb, _ in rows], ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def knn_env(monkeypatch, tmp_path):
    """临时索引 + 假编码器环境；用例内配置查询向量。"""

    def setup(rows, query_vec):
        _make_index(tmp_path, rows)
        monkeypatch.setattr(settings, "INTENT_EXAMPLE_INDEX_DIR", str(tmp_path))
        example_knn.reset_for_test()
        monkeypatch.setattr(
            example_knn, "setfit_intent_model", _FakeEncoder(query_vec)
        )
        return example_knn.example_knn_index

    yield setup
    example_knn.reset_for_test()


# ---------------- 索引查询 ----------------


def test_query_majority_label_and_similarity(knn_env):
    index = knn_env(
        rows=[
            ("LOGISTICS.TRACK", [1.0, 0.0, 0.0]),
            ("LOGISTICS.TRACK", [0.99, 0.1, 0.0]),
            ("ORDER.QUERY_STATUS", [0.0, 1.0, 0.0]),
        ],
        query_vec=[1.0, 0.0, 0.0],
    )
    result = index.query("东西到哪了")
    assert result is not None
    assert result["label"] == "LOGISTICS.TRACK"
    assert result["similarity"] > 0.9
    assert result["votes"].startswith("2/")


def test_query_missing_index_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INTENT_EXAMPLE_INDEX_DIR", str(tmp_path / "nope"))
    example_knn.reset_for_test()
    try:
        assert example_knn.example_knn_index.query("任何话") is None
    finally:
        example_knn.reset_for_test()


def test_query_corrupt_index_disabled(monkeypatch, tmp_path):
    """行数与标签数不齐 → 视为损坏停用，不抛异常。"""
    np.save(tmp_path / "embeddings.npy", np.zeros((3, 4), dtype=np.float32))
    (tmp_path / "labels.json").write_text('["A"]', encoding="utf-8")
    monkeypatch.setattr(settings, "INTENT_EXAMPLE_INDEX_DIR", str(tmp_path))
    example_knn.reset_for_test()
    try:
        assert example_knn.example_knn_index.query("x") is None
    finally:
        example_knn.reset_for_test()


# ---------------- 混合分类器接入（margin 小分支） ----------------


class _FakeModel:
    available = True

    def predict(self, text: str, top_k: int = 3):
        top = [
            {"label": "LOGISTICS.TRACK", "score": 0.78},
            {"label": "ORDER.QUERY_STATUS", "score": 0.76},
        ]
        return top[0]["label"], top[0]["score"], top


@pytest.fixture
def _small_margin_model(monkeypatch):
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.setfit_intent_model", _FakeModel()
    )


async def test_disabled_flag_keeps_low_margin_path(_small_margin_model):
    """默认关闭：margin 小仍走 SETFIT_LOW_MARGIN（零回归）。"""
    assert settings.INTENT_EXAMPLE_KNN_ENABLED is False
    r = await hybrid_intent_classifier.classify("这个订单的东西什么情况")
    assert r.decision_source == DecisionSource.SETFIT_LOW_MARGIN
    assert r.example_knn is None


async def test_knn_confirm_skips_second_opinion(_small_margin_model, monkeypatch):
    """近邻同意 top1 且相似度达标 → 免二判采纳 SETFIT_KNN_CONFIRMED。"""
    monkeypatch.setattr(settings, "INTENT_EXAMPLE_KNN_ENABLED", True)
    monkeypatch.setattr(
        example_knn.example_knn_index,
        "query",
        lambda text: {"label": "LOGISTICS.TRACK", "similarity": 0.88, "votes": "5/5", "top": []},
    )
    r = await hybrid_intent_classifier.classify("这个订单的东西什么情况")
    assert r.pred_label == "LOGISTICS.TRACK"  # 绝不改选
    assert r.decision_source == DecisionSource.SETFIT_KNN_CONFIRMED
    assert r.example_knn["similarity"] == 0.88
    assert r.to_dict()["example_knn"]["votes"] == "5/5"


async def test_knn_disagree_keeps_second_opinion_path(_small_margin_model, monkeypatch):
    """近邻不同意 → 不改选，证据附加，仍走二判路径（无 Key 落 LOW_MARGIN）。"""
    monkeypatch.setattr(settings, "INTENT_EXAMPLE_KNN_ENABLED", True)
    monkeypatch.setattr(
        example_knn.example_knn_index,
        "query",
        lambda text: {"label": "ORDER.QUERY_STATUS", "similarity": 0.9, "votes": "4/5", "top": []},
    )
    r = await hybrid_intent_classifier.classify("这个订单的东西什么情况")
    assert r.pred_label == "LOGISTICS.TRACK"  # 仍是 top1
    assert r.decision_source == DecisionSource.SETFIT_LOW_MARGIN
    assert r.example_knn["label"] == "ORDER.QUERY_STATUS"  # 分歧证据落库


async def test_knn_low_similarity_not_confirmed(_small_margin_model, monkeypatch):
    """近邻同意但相似度不达标 → 不确认。"""
    monkeypatch.setattr(settings, "INTENT_EXAMPLE_KNN_ENABLED", True)
    monkeypatch.setattr(
        example_knn.example_knn_index,
        "query",
        lambda text: {"label": "LOGISTICS.TRACK", "similarity": 0.40, "votes": "3/5", "top": []},
    )
    r = await hybrid_intent_classifier.classify("这个订单的东西什么情况")
    assert r.decision_source == DecisionSource.SETFIT_LOW_MARGIN


# ---------------- 低置信闲聊救援（家常开放域优化） ----------------


class _LowConfModel:
    """低置信模型：top1 标签可配（模拟家常→CHITCHAT 低分 / 业务低分）。"""

    available = True

    def __init__(self, label: str, score: float = 0.30):
        self._label, self._score = label, score

    def predict(self, text: str, top_k: int = 3):
        top = [
            {"label": self._label, "score": self._score},
            {"label": "FAQ.GENERAL", "score": 0.20},
        ]
        return self._label, self._score, top


async def test_chitchat_rescue_skips_llm(monkeypatch):
    """家常：top1=CHITCHAT 低置信 + 近邻高相似同意 → 免二判直接采纳。"""
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.setfit_intent_model",
        _LowConfModel("CHITCHAT.GENERAL"),
    )
    monkeypatch.setattr(settings, "INTENT_EXAMPLE_KNN_ENABLED", True)
    monkeypatch.setattr(
        example_knn.example_knn_index,
        "query",
        lambda text: {"label": "CHITCHAT.GENERAL", "similarity": 0.82, "votes": "5/5", "top": []},
    )
    r = await hybrid_intent_classifier.classify("今天天气是真的热啊")
    assert r.pred_label == "CHITCHAT.GENERAL"
    assert r.decision_source == DecisionSource.SETFIT_KNN_CHITCHAT
    assert r.example_knn["similarity"] == 0.82


async def test_business_intent_never_rescued_at_low_conf(monkeypatch):
    """红线：业务意图低置信即使近邻同意也不救援（走二判/UNKNOWN）。"""
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.setfit_intent_model",
        _LowConfModel("AFTERSALE.REFUND"),
    )
    monkeypatch.setattr(settings, "INTENT_EXAMPLE_KNN_ENABLED", True)
    monkeypatch.setattr(
        example_knn.example_knn_index,
        "query",
        lambda text: {"label": "AFTERSALE.REFUND", "similarity": 0.95, "votes": "5/5", "top": []},
    )
    r = await hybrid_intent_classifier.classify("那个事儿弄一下呗")
    assert r.pred_label == "META.UNKNOWN"  # 无 Key 二判不可用 → 兜底
    assert r.decision_source == DecisionSource.SETFIT_LOW_CONF


async def test_chitchat_rescue_requires_knn_agreement(monkeypatch):
    """近邻不同意 top1（说像业务）→ 不救援，走原路径。"""
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.setfit_intent_model",
        _LowConfModel("CHITCHAT.GENERAL"),
    )
    monkeypatch.setattr(settings, "INTENT_EXAMPLE_KNN_ENABLED", True)
    monkeypatch.setattr(
        example_knn.example_knn_index,
        "query",
        lambda text: {"label": "LOGISTICS.TRACK", "similarity": 0.88, "votes": "4/5", "top": []},
    )
    r = await hybrid_intent_classifier.classify("咋还不见动静呢")
    assert r.decision_source == DecisionSource.SETFIT_LOW_CONF


async def test_chitchat_rescue_similarity_line(monkeypatch):
    """相似度低于救援线（0.70，高于 margin 确认线）→ 不救援。"""
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.setfit_intent_model",
        _LowConfModel("CHITCHAT.GENERAL"),
    )
    monkeypatch.setattr(settings, "INTENT_EXAMPLE_KNN_ENABLED", True)
    monkeypatch.setattr(
        example_knn.example_knn_index,
        "query",
        lambda text: {"label": "CHITCHAT.GENERAL", "similarity": 0.66, "votes": "3/5", "top": []},
    )
    r = await hybrid_intent_classifier.classify("哈哈哈行吧")
    assert r.decision_source == DecisionSource.SETFIT_LOW_CONF


async def test_chitchat_rescue_off_by_default(monkeypatch):
    """默认关闭（随 KNN 总开关）：低置信闲聊仍走原路径，零回归。"""
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.setfit_intent_model",
        _LowConfModel("CHITCHAT.GENERAL"),
    )
    assert settings.INTENT_EXAMPLE_KNN_ENABLED is False
    r = await hybrid_intent_classifier.classify("今天天气是真的热啊")
    assert r.decision_source == DecisionSource.SETFIT_LOW_CONF
