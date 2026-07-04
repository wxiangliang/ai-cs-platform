"""HybridIntentClassifier 单元测试（monkeypatch SetFit 模型，不依赖真实权重）。"""

import pytest

from app.chat.intent.hybrid_classifier import hybrid_intent_classifier
from app.chat.intent.setfit_classifier import setfit_intent_model
from app.chat.intent.types import DecisionSource, IntentLabel
from app.chat.state.types import DialogStateValue


class _FakeModel:
    """可控的假 SetFit 模型。"""

    def __init__(self, label: str, score: float, available: bool = True):
        self._label, self._score, self._available = label, score, available

    @property
    def available(self) -> bool:
        return self._available

    def predict(self, text: str, top_k: int = 3):
        top = [{"label": self._label, "score": self._score}]
        return self._label, self._score, top


@pytest.fixture
def _patch_model(monkeypatch):
    def apply(label="LOGISTICS.TRACK", score=0.9, available=True):
        fake = _FakeModel(label, score, available)
        monkeypatch.setattr(
            "app.chat.intent.hybrid_classifier.setfit_intent_model", fake
        )
        return fake

    return apply


async def test_control_layer_shortcuts_before_model(_patch_model):
    """控制层意图（取消订单/转人工/确认门）不进模型。"""
    _patch_model(label="PRODUCT.ASK_INFO", score=0.99)
    r = await hybrid_intent_classifier.classify("我要取消订单")
    assert r.pred_label == IntentLabel.ORDER_CANCEL
    assert r.decision_source == DecisionSource.RULE_KEYWORD

    r = await hybrid_intent_classifier.classify("转人工")
    assert r.pred_label == IntentLabel.META_TRANSFER_HUMAN

    r = await hybrid_intent_classifier.classify(
        "确认", current_state=DialogStateValue.CONFIRMING
    )
    assert r.pred_label == IntentLabel.META_CONFIRM


async def test_semantic_layer_high_confidence(_patch_model):
    _patch_model(label="LOGISTICS.TRACK", score=0.91)
    r = await hybrid_intent_classifier.classify("东西还没到我等急了")
    assert r.pred_label == "LOGISTICS.TRACK"
    assert r.decision_source == DecisionSource.SETFIT
    assert r.top_k


async def test_semantic_layer_low_confidence_falls_to_unknown(_patch_model):
    _patch_model(label="PRODUCT.ASK_PRICE", score=0.30)
    r = await hybrid_intent_classifier.classify("嗯嗯那个啥来着")
    assert r.pred_label == IntentLabel.META_UNKNOWN
    assert r.decision_source == DecisionSource.SETFIT_LOW_CONF
    # top_k 保留供排查与数据回流
    assert r.top_k[0]["label"] == "PRODUCT.ASK_PRICE"


async def test_model_unavailable_degrades_to_rule(_patch_model):
    _patch_model(available=False)
    r = await hybrid_intent_classifier.classify("我要退款")
    # 降级规则全表：关键词能接住业务意图
    assert r.pred_label == IntentLabel.AFTERSALE_REFUND
    assert r.decision_source == DecisionSource.SETFIT_FALLBACK_RULE


async def test_real_model_lazy_load_missing_path(monkeypatch):
    """真实 SetFitIntentModel：路径不存在时 available=False，不抛异常。"""
    from app.chat.intent.setfit_classifier import SetFitIntentModel

    model = SetFitIntentModel(model_path="models/__not_exists__")
    assert model.available is False
    assert setfit_intent_model is not None  # 单例可导入