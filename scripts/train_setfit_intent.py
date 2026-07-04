"""SetFit 意图分类器训练脚本。

策略（CPU 环境，见 stage-04-02 需求第 3 节）：
1. body 对比学习微调：每类采样子集（默认 16 条），限制步数，控制训练时长；
2. 分类头（LogisticRegression）：用全量 train 集 embedding 重新拟合（SetFit 标准做法）；
3. test 集评估：整体 accuracy / macro-F1 + 逐类 F1 + 易混淆组混淆矩阵；
4. 产物保存到 models/intent_setfit_v1/（模型 + metrics.json + labels.json，不入 git）。

用法：uv run python scripts/train_setfit_intent.py [--base-model BAAI/bge-small-zh-v1.5]
"""

import argparse
import csv
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path("docs/intent/intent_train_v42_project.csv")
OUT_DIR = Path("models/intent_setfit_v1")

# 易混淆组（taxonomy 第 6 节），单独输出混淆矩阵
CONFUSION_GROUPS = [
    ["AFTERSALE.REFUND", "AFTERSALE.RETURN", "ORDER.CANCEL"],
    ["LOGISTICS.TRACK", "LOGISTICS.DELIVERY_TIME", "ORDER.QUERY_STATUS"],
    ["PRODUCT.ASK_INFO", "FAQ.GENERAL"],
]


def load_split(split: str) -> tuple[list[str], list[str]]:
    texts, labels = [], []
    for r in csv.DictReader(DATA.open(encoding="utf-8")):
        if r["split"] == split:
            texts.append(r["text"])
            labels.append(r["intent"])
    return texts, labels


def sample_per_class(texts: list[str], labels: list[str], k: int, seed: int = 42):
    """每类采样 k 条用于 body 对比学习微调。"""
    by_label = defaultdict(list)
    for t, y in zip(texts, labels):
        by_label[y].append(t)
    rng = random.Random(seed)
    s_texts, s_labels = [], []
    for y, ts in sorted(by_label.items()):
        for t in rng.sample(ts, min(k, len(ts))):
            s_texts.append(t)
            s_labels.append(y)
    return s_texts, s_labels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--samples-per-class", type=int, default=16)
    parser.add_argument("--max-steps", type=int, default=400)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    from datasets import Dataset
    from setfit import SetFitModel, Trainer, TrainingArguments
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

    train_texts, train_labels = load_split("train")
    test_texts, test_labels = load_split("test")
    label_list = sorted(set(train_labels))
    print(f"train={len(train_texts)} test={len(test_texts)} classes={len(label_list)}")

    t0 = time.time()
    model = SetFitModel.from_pretrained(args.base_model, labels=label_list)

    # —— 1. body 对比学习微调（每类子集，限步数）——
    s_texts, s_labels = sample_per_class(train_texts, train_labels, args.samples_per_class)
    train_ds = Dataset.from_dict({"text": s_texts, "label": s_labels})
    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            batch_size=args.batch_size,
            num_epochs=1,
            max_steps=args.max_steps,
            sampling_strategy="oversampling",
            report_to="none",
        ),
        train_dataset=train_ds,
    )
    trainer.train()
    print(f"body fine-tune done in {time.time() - t0:.0f}s")

    # —— 2. 分类头用全量 train 集重新拟合 ——
    t1 = time.time()
    emb = model.model_body.encode(
        train_texts, batch_size=128, show_progress_bar=True, normalize_embeddings=True
    )
    model.model_head.fit(emb, train_labels)
    print(f"head fit on full train done in {time.time() - t1:.0f}s")

    # —— 3. test 集评估 ——
    t2 = time.time()
    test_emb = model.model_body.encode(
        test_texts, batch_size=128, show_progress_bar=True, normalize_embeddings=True
    )
    preds = model.model_head.predict(test_emb).tolist()
    acc = accuracy_score(test_labels, preds)
    macro_f1 = f1_score(test_labels, preds, average="macro")
    per_class_f1 = {
        y: round(s, 4)
        for y, s in zip(label_list, f1_score(test_labels, preds, average=None, labels=label_list))
    }
    groups = []
    for group in CONFUSION_GROUPS:
        idx = [i for i, y in enumerate(test_labels) if y in group]
        g_true = [test_labels[i] for i in idx]
        g_pred = [preds[i] if preds[i] in group else "OTHER" for i in idx]
        cm = confusion_matrix(g_true, g_pred, labels=group + ["OTHER"]).tolist()
        groups.append({"labels": group + ["OTHER"], "matrix": cm})

    metrics = {
        "base_model": args.base_model,
        "data": str(DATA),
        "train_size": len(train_texts),
        "test_size": len(test_texts),
        "classes": len(label_list),
        "samples_per_class_body": args.samples_per_class,
        "max_steps": args.max_steps,
        "accuracy": round(acc, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class_f1": per_class_f1,
        "confusion_groups": groups,
        "test_label_dist": dict(Counter(test_labels)),
        "train_seconds": round(time.time() - t0),
    }
    print(f"\naccuracy={acc:.4f} macro_f1={macro_f1:.4f} eval in {time.time() - t2:.0f}s")
    worst = sorted(per_class_f1.items(), key=lambda x: x[1])[:5]
    print("worst classes:", worst)

    # —— 4. 保存产物 ——
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(OUT_DIR))
    (OUT_DIR / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "labels.json").write_text(
        json.dumps(label_list, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"saved -> {OUT_DIR}")


if __name__ == "__main__":
    main()
