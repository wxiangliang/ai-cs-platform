"""示例向量索引构建脚本（示例向量交叉验证的离线产物，默认功能关闭）。

    uv run python scripts/build_intent_example_index.py [--per-class-cap 200]

从意图训练集（train split）采样每类若干条，用 SetFit 语义体编码为归一化
向量，产物落 models/intent_example_index/（不进 git）：

    embeddings.npy   # float32 (n, dim)，行 L2 归一化
    labels.json      # 与行对齐的意图码列表
    meta.json        # 构建参数与统计（排查用）

启用链路（见 docs/intent/README.md「示例向量交叉验证与 LTR 路线」）：
建索引 → INTENT_EXAMPLE_KNN_ENABLED=true → 跑意图评估门禁确认零回归。
模型换版（重训 SetFit）后**必须重建索引**——分类与近邻必须同源表示。
"""

import argparse
import csv
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA = Path("docs/intent/intent_train_v42_project.csv")
OUT_DIR = Path("models/intent_example_index")
SEED = 42


def main() -> None:
    parser = argparse.ArgumentParser(description="构建意图示例向量索引")
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument(
        "--per-class-cap", type=int, default=200,
        help="每类最多采样条数（控制索引体积与查询延迟）",
    )
    args = parser.parse_args()

    from app.chat.intent.setfit_classifier import setfit_intent_model

    if not setfit_intent_model.available:
        raise SystemExit("SetFit 模型不可用（models/intent_setfit_v1 缺失）——索引必须与分类器同源，先训模型")

    # 每类采样 train split（固定种子可复现）
    by_label: dict[str, list[str]] = defaultdict(list)
    with Path(args.data).open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["split"] == "train":
                by_label[row["intent"]].append(row["text"])
    rng = random.Random(SEED)
    texts: list[str] = []
    labels: list[str] = []
    for label, pool in sorted(by_label.items()):
        for text in rng.sample(pool, min(args.per_class_cap, len(pool))):
            texts.append(text)
            labels.append(label)
    print(f"采样 {len(texts)} 条示例（{len(by_label)} 类，每类上限 {args.per_class_cap}）")

    # 批量编码（复用 SetFit 语义体，归一化向量）
    import numpy as np

    model = setfit_intent_model._model  # noqa: SLF001 - 离线脚本直取模型体批量编码
    start = time.time()
    matrix = model.model_body.encode(
        texts, normalize_embeddings=True, batch_size=128, show_progress_bar=True
    ).astype(np.float32)
    print(f"编码完成：{matrix.shape}，耗时 {time.time() - start:.1f}s")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "embeddings.npy", matrix)
    (out / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False), encoding="utf-8"
    )
    (out / "meta.json").write_text(
        json.dumps(
            {
                "source": args.data,
                "per_class_cap": args.per_class_cap,
                "count": len(labels),
                "dim": int(matrix.shape[1]),
                "classes": sorted(by_label),
                "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"索引已写入 {out}/（embeddings.npy / labels.json / meta.json）")
    print("启用：.env 配置 INTENT_EXAMPLE_KNN_ENABLED=true，然后跑意图评估门禁确认零回归")


if __name__ == "__main__":
    main()
