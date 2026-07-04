"""多意图切分器评估集（来源：v41 的 13 条复合句样本，sample_type=multi_intent）。

这些样本 trainable=False，本来就是为切分器准备的评估数据（不进分类器训练——
分段后每段是单意图，正是现有训练分布，故 v42 训练集无需改动，见 intent README）。

段级语义分类依赖 SetFit 模型，模型产物缺失时显式 skip。
期望值按 taxonomy v2 规范码与判定优先级编写（如「也要转人工」→ 控制意图整句优先）。
"""

from pathlib import Path

import pytest

from app.chat.intent.multi_intent import detect_multi_intent
from app.chat.state.types import DialogStateValue

MODEL_DIR = Path("models/intent_setfit_v1")

pytestmark = pytest.mark.skipif(
    not (MODEL_DIR / "config.json").exists(),
    reason="SetFit 模型产物不存在——本评估被跳过，不代表通过",
)

# (文本, 期望主意图集合(任一即可), 期望 pending 意图集合(任一命中即可)或 None=期望不拆/无 pending)
CASES = [
    ("这个多少钱，有白色的吗，能便宜点不",
     {"PRODUCT.ASK_PRICE"}, {"PRODUCT.ASK_INFO", "PRODUCT.ASK_STOCK"}),
    ("帮我查下订单到哪了，另外发票什么时候开",
     {"LOGISTICS.TRACK", "ORDER.QUERY_STATUS"}, {"PAYMENT.INVOICE"}),
    ("我要退款，顺便问下运费谁承担",
     {"AFTERSALE.REFUND"}, {"LOGISTICS.SHIPPING_FEE", "FAQ.GENERAL"}),
    ("订单 A12345 改成公司地址，再帮我看下什么时候发货",
     {"ORDER.CHANGE_ADDRESS"}, {"LOGISTICS.DELIVERY_TIME", "ORDER.QUERY_STATUS"}),
    ("这个多少钱，有没有库存",
     {"PRODUCT.ASK_PRICE"}, {"PRODUCT.ASK_STOCK"}),
    ("发票能开吗，顺便查下快递",
     {"PAYMENT.INVOICE"}, {"LOGISTICS.TRACK"}),
    ("我要退货，但是也想换个颜色",
     {"AFTERSALE.RETURN"}, {"AFTERSALE.EXCHANGE"}),
    ("帮我取消订单，再问一下退款多久到",
     {"ORDER.CANCEL"}, {"AFTERSALE.REFUND", "FAQ.GENERAL"}),
    ("有优惠券吗，能不能再便宜一点",
     {"PROMOTION.COUPON"}, {"PRODUCT.ASK_PRICE"}),
    ("这款有白色吗，尺码准不准",
     {"PRODUCT.ASK_INFO", "PRODUCT.ASK_STOCK"}, {"PRODUCT.ASK_INFO", "FAQ.GENERAL"}),
    # 同意图多子句：不应拆出第二任务
    ("订单到哪了，为什么一直不更新", {"LOGISTICS.TRACK"}, None),
    # 控制意图整句优先：不拆分（None 表示 detect 返回 None，整句交控制层）
    ("我要投诉，也要转人工", None, None),
    ("小米和格力哪个好，哪个更省电", {"PRODUCT.COMPARE"}, None),
]


@pytest.fixture(scope="module")
def classifier():
    from app.chat.intent.hybrid_classifier import hybrid_intent_classifier

    return hybrid_intent_classifier


async def test_multi_intent_eval_set(classifier):
    passed, failures = 0, []
    for text, expect_primary, expect_pending in CASES:
        result = await detect_multi_intent(
            text, classifier,
            current_state=DialogStateValue.IDLE, has_active_task=False,
        )
        if expect_primary is None:
            ok = result is None
        elif expect_pending is None:
            # 不要求拆分：拆了但主意图正确且无错误 pending 也算过
            ok = result is None or (
                result.primary.pred_label in expect_primary and not result.pending
            )
        else:
            ok = (
                result is not None
                and result.primary.pred_label in expect_primary
                and any(p["intent"] in expect_pending for p in result.pending)
            )
        if ok:
            passed += 1
        else:
            got = (
                "不拆分"
                if result is None
                else f"{result.primary.pred_label}+{[p['intent'] for p in result.pending]}"
            )
            failures.append(f"{text!r} -> {got}")
    # 评估门槛：13 条中至少 10 条符合预期（切分+段级语义分类的联合正确率）
    assert passed >= 10, f"多意图评估 {passed}/13 通过，失败样本：\n" + "\n".join(failures)
