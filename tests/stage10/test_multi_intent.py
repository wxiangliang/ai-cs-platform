"""多意图检测单元测试（规则分类器离线可跑）。"""

from app.chat.intent.multi_intent import detect_multi_intent
from app.chat.intent.rule_classifier import rule_intent_classifier
from app.chat.intent.types import IntentLabel
from app.chat.state.types import DialogStateValue


async def _detect(text: str, state: str = DialogStateValue.IDLE):
    return await detect_multi_intent(
        text, rule_intent_classifier, current_state=state, has_active_task=False
    )


async def test_two_intents_split():
    r = await _detect("我要退款，顺便查下这个订单的物流到哪了")
    assert r is not None
    assert r.primary.pred_label == IntentLabel.AFTERSALE_REFUND  # 主意图=第一段
    assert [p["intent"] for p in r.pending] == [IntentLabel.LOGISTICS_TRACK]


async def test_slots_stay_in_their_segment():
    """「退款订单A1…，再看下B2的物流」：槽位各归其主，防串槽。"""
    r = await _detect("我要退款订单号A11111111，再看下B22222222的物流")
    assert r is not None
    assert "A11111111" in r.primary_text and "B22222222" not in r.primary_text
    assert r.pending[0]["slots"].get("order_id") == "B22222222"


async def test_single_intent_not_split():
    # 无并列标记 / 同一意图多段 → 不拆
    assert await _detect("我要退款") is None
    assert await _detect("我要退款，然后把钱退给我") is None  # 两段同为退款


async def test_control_intent_wins():
    # 「算了，都不要了」整句控制意图，不拆分
    assert await _detect("算了，然后都不用了") is None


async def test_chitchat_segment_ignored():
    r = await _detect("你好，另外帮我查下物流到哪了，谢谢")
    # 只剩一个业务意图 → 不构成多意图
    assert r is None
