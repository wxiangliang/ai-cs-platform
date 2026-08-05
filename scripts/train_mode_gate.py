"""Conversation Mode Gate 训练脚本（Stage 30，操作文档 docs/intent/mode_gate_training.md）。

用 SetFit 意图模型的 body 编码（**与线上同源表示**——分类与模式判定共享
embedding，SetFit 重训后本模型必须重训）训练四分类 LR 头
（SOCIAL_ONLY/TASK_ONLY/MIXED/OOS）+ Platt 概率校准（val 集拟合）。

    uv run python scripts/train_mode_gate.py

纪律：
- UNCERTAIN 是推理拒识状态不是训练标签（数据中不存在，加载时断言）；
- 首要指标 SOCIAL_ONLY precision（业务误吞成闲聊是最高代价错误），
  不按总 Accuracy 决策上线；
- MIXED/OOS 为合成冷启动数据（review_status 标记），离线高分不构成
  接管范围扩大的依据（v1 只接管高置信 SOCIAL_ONLY）。
"""

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA = Path("docs/intent/intent_mode_v43_package/conversation_mode_train_v1.csv")
OUT_DIR = Path("models/mode_gate_v1")

MODE_LABELS = {"SOCIAL_ONLY", "TASK_ONLY", "MIXED", "OOS"}
LABEL_COLUMN = "conversation_mode"


def load_split(path: Path, split: str) -> tuple[list[str], list[str]]:
    """读取指定 split 的 (texts, labels)；标签必须在四类集合内。"""
    texts, labels = [], []
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        if r["split"] != split:
            continue
        label = r[LABEL_COLUMN]
        assert label in MODE_LABELS, f"意外模式标签（UNCERTAIN 不是训练标签）: {label}"
        texts.append(r["text"])
        labels.append(label)
    return texts, labels


def main() -> None:
    parser = argparse.ArgumentParser(description="Mode Gate 训练（Stage 30）")
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        confusion_matrix,
        f1_score,
    )

    from app.chat.intent.setfit_classifier import setfit_intent_model

    assert setfit_intent_model.available, (
        "SetFit 意图模型产物不存在（先运行 scripts/train_setfit_intent.py）——"
        "mode head 必须与线上分类器共享同一 body 表示"
    )
    body = setfit_intent_model._model.model_body  # noqa: SLF001 - 训练脚本复用推理单例的 body

    data_path = Path(args.data)
    xt_text, yt = load_split(data_path, "train")
    xv_text, yv = load_split(data_path, "val")
    xs_text, ys = load_split(data_path, "test")
    print(f"train={len(yt)} val={len(yv)} test={len(ys)} dist={dict(Counter(yt))}")

    t0 = time.time()
    encode = lambda texts: body.encode(  # noqa: E731
        texts, batch_size=128, show_progress_bar=True, normalize_embeddings=True
    )
    xt, xv, xs = encode(xt_text), encode(xv_text), encode(xs_text)
    print(f"encode done in {time.time() - t0:.0f}s")

    # —— LR 四分类 + Platt 校准（val 集拟合；运行时阈值 0.88 需有
    # 「≈88% 正确率」的概率语义，未校准的 LR 分数偏自信做不到）——
    lr = LogisticRegression(max_iter=3000, class_weight="balanced", random_state=42)
    lr.fit(xt, yt)
    calibrated = "none"
    model = lr
    try:
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.frozen import FrozenEstimator

        model = CalibratedClassifierCV(FrozenEstimator(lr), method="sigmoid")
        model.fit(xv, yv)
        calibrated = "sigmoid_on_val"
    except Exception as exc:  # noqa: BLE001 - 校准失败降级未校准 LR，并在 spec 标记
        print(f"[warn] calibration failed ({exc!r}), fallback to raw LR")
        model = lr

    preds = list(model.predict(xs))
    acc = accuracy_score(ys, preds)
    macro_f1 = f1_score(ys, preds, average="macro")
    report = classification_report(ys, preds, output_dict=True, zero_division=0)
    labels_sorted = sorted(MODE_LABELS)
    cm = confusion_matrix(ys, preds, labels=labels_sorted).tolist()

    social = report.get("SOCIAL_ONLY", {})
    print(f"\naccuracy={acc:.4f} macro_f1={macro_f1:.4f} calibrated={calibrated}")
    print(
        f"SOCIAL_ONLY precision={social.get('precision', 0):.4f} "
        f"recall={social.get('recall', 0):.4f}  ← 首要指标（业务误吞代价最高）"
    )
    print("per-class:")
    for label in labels_sorted:
        r = report.get(label, {})
        print(
            f"  {label:12s} P={r.get('precision', 0):.4f} "
            f"R={r.get('recall', 0):.4f} F1={r.get('f1-score', 0):.4f}"
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_dir / "mode_head.joblib")
    (out_dir / "mode_spec.json").write_text(
        json.dumps(
            {
                "labels": labels_sorted,
                "data": str(data_path),
                "embedding_source": setfit_intent_model._model_path,  # noqa: SLF001
                "calibration": calibrated,
                "train_size": len(yt),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(
            {
                "accuracy": round(acc, 4),
                "macro_f1": round(macro_f1, 4),
                "calibration": calibrated,
                "per_class": {
                    k: {m: round(v[m], 4) for m in ("precision", "recall", "f1-score")}
                    for k, v in report.items()
                    if k in MODE_LABELS
                },
                "confusion_matrix": {"labels": labels_sorted, "matrix": cm},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"saved -> {out_dir}（mode_head.joblib / mode_spec.json / metrics.json）")


if __name__ == "__main__":
    main()
