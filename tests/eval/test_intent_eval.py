"""意图评估回归（Stage 04-01，规范见 docs/testing/intent_eval_set.md）。

两部分：
1. 控制层回归：固定对抗样例，纯规则可离线跑，任何改动分类器都必须通过；
2. SetFit 语义层回归：v42 test 集整体准确率下限（模型产物存在时跑，
   不存在时 skip 并显式提示——不允许静默当作通过）。
"""

import csv
from pathlib import Path

import pytest

from app.chat.intent.rule_classifier import rule_intent_classifier
from app.chat.state.types import DialogStateValue

DATA = Path("docs/intent/intent_train_v42_project.csv")
MODEL_DIR = Path("models/intent_setfit_v1")

# 控制层对抗样例：(文本, 状态上下文, 期望意图)——taxonomy 第 4/6 节裁决规则的固化
CONTROL_CASES = [
    ("我要取消订单", None, "ORDER.CANCEL"),
    ("帮我取消一下这单", None, "ORDER.CANCEL"),
    ("算了不用了", None, "META.ABORT"),
    ("转人工", None, "META.TRANSFER_HUMAN"),
    ("你是机器人吗", None, "META.BOT_IDENTITY"),
    ("A12345678", None, "META.SLOT_ONLY"),
    ("13800138000", None, "META.SLOT_ONLY"),
    ("thank you", None, "CHITCHAT.THANKS"),
    ("确认", DialogStateValue.CONFIRMING, "META.CONFIRM"),
    ("对的没问题", DialogStateValue.CONFIRMING, "META.CONFIRM"),
    ("先不了", DialogStateValue.CONFIRMING, "META.DENY"),
    ("确认", None, "META.UNKNOWN"),  # 非确认门上下文不得输出 CONFIRM
    # —— 控制层误判修复回归（2026-07-27）：长句夹带诉求/被动式/身份询问 ——
    ("你是真人吗", None, "META.BOT_IDENTITY"),  # 问身份，不得被「真人」误吞成转人工
    ("我要真人客服", None, "META.TRANSFER_HUMAN"),  # 真要人工的仍要能转
    ("算了，还是帮我退款吧", None, "AFTERSALE.REFUND"),  # 「算了」是转折词不是放弃
    ("算了，然后都不用了", None, "META.ABORT"),  # 长句纯放弃仍判 ABORT
    ("订单被取消了是怎么回事", None, "ORDER.QUERY_STATUS"),  # 被动式是状态咨询不是取消请求
]

# 这些句子含控制层关键词但语义是业务咨询，控制层必须放行（返回 None）
# 交给语义层——否则 SetFit/LLM 永远没机会纠正（修复前全部被误短路）
CONTROL_PASSTHROUGH_CASES = [
    "活动什么时候结束",
    "怎么取消自动续费",
    "不用了谢谢，问下退货政策",
    "被取消的订单能恢复吗",
    "订单已取消了吗",
    "订单怎么被取消了",
]


async def test_control_layer_adversarial_cases():
    failures = []
    for text, ctx, expect in CONTROL_CASES:
        kwargs = {"current_state": ctx} if ctx else {}
        result = await rule_intent_classifier.classify(text, **kwargs)
        if result.pred_label != expect:
            failures.append(f"{text!r}(ctx={ctx}) -> {result.pred_label}, expect {expect}")
    assert not failures, "控制层对抗样例回归失败：\n" + "\n".join(failures)


def test_control_layer_passthrough_cases():
    """含控制关键词的业务咨询不得被控制层短路（必须到达语义层）。"""
    failures = []
    for text in CONTROL_PASSTHROUGH_CASES:
        result = rule_intent_classifier.classify_control(text)
        if result is not None:
            failures.append(f"{text!r} -> {result.pred_label}, expect None(放行语义层)")
    assert not failures, "控制层放行回归失败：\n" + "\n".join(failures)


@pytest.mark.skipif(
    not (MODEL_DIR / "model_head.pkl").exists() and not (MODEL_DIR / "config.json").exists(),
    reason="SetFit 模型产物不存在（先运行 scripts/train_setfit_intent.py）——本项被跳过，不代表通过",
)
def test_setfit_test_split_accuracy():
    from setfit import SetFitModel

    rows = [r for r in csv.DictReader(DATA.open(encoding="utf-8")) if r["split"] == "test"]
    texts = [r["text"] for r in rows]
    labels = [r["intent"] for r in rows]

    model = SetFitModel.from_pretrained(str(MODEL_DIR))
    emb = model.model_body.encode(texts, batch_size=128, normalize_embeddings=True)
    preds = model.model_head.predict(emb)
    acc = sum(p == y for p, y in zip(preds, labels)) / len(labels)
    # 验收线（stage-04-02 需求第 3 节）：整体 accuracy ≥ 0.90
    assert acc >= 0.90, f"SetFit test 集准确率回退：{acc:.4f} < 0.90"
