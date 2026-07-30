"""Meta-classifier 训练脚本（Stage 27 v1 离线管线）。

学习目标不是意图，而是**对当前任务的操作决策**（续接/切换/接受新意图/
送 LLM 二判/澄清/未知）——即 Stage 26 手调阈值（切换守护/margin 路由）
承担的那个决策。规则系统不被替代：控制层在模型前短路、确认门在模型后兜底。

用法（见 docs/intent/meta_classifier_training.md）：
    uv sync --group train                       # lightgbm/xgboost/pandas
    uv run python scripts/train_meta_classifier.py [--scope deploy|full]
                                                 [--models lr,tree,lgbm,xgb]

纪律（stage-27 文档 4.2/4.4）：
- 特征白名单/黑名单以本文件顶部常量为单一事实来源（契约测试锁定）；
- 部署域 = control_result==NONE（规则域运行时到不了 Meta 层）；
- 冠军按代价加权错误选（误切 ×5 > 误吞 ×3 > 漏二判 ×2 > 多花 LLM ×1），
  但合成数据上的冠军**不构成上线依据**（SetFit 分数是模拟的）。
"""

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 特征契约（单一事实来源；改动须同步 tests/stage27 与操作文档第 2 节）
# ---------------------------------------------------------------------------

# 泄漏黑名单：llm_review_expected 与 SEND_TO_LLM 标签 100% 同向（标签改写）；
# message 去重后仅 1273 条、单条重复至 200 次（文本特征必背标签）；
# 其余为生成元数据 / 标签侧 / 域过滤字段
FORBIDDEN_COLUMNS = frozenset(
    {
        "row_id", "split", "case_family_id", "scenario_family", "message",
        "control_result", "llm_review_expected", "feature_source", "notes",
        "sample_weight", "target_decision", "target_intent", "target_domain",
        "target_risk_level", "target_priority",
    }
)

CATEGORICAL_FEATURES = [
    "current_state", "active_intent", "active_domain", "pending_slot",
    "slot_match_type", "slot_value_type", "setfit_top1_label", "setfit_top2_label",
]
BOOLEAN_FEATURES = [
    "has_active_task", "slot_match", "explicit_switch_signal",
    "has_business_object", "setfit_low_conf", "setfit_ambiguous",
]
NUMERIC_FEATURES = [
    "suspended_task_count", "setfit_top1_score", "setfit_top2_score", "setfit_margin",
]
ALL_FEATURES = CATEGORICAL_FEATURES + BOOLEAN_FEATURES + NUMERIC_FEATURES

LABEL_COLUMN = "target_decision"
WEIGHT_COLUMN = "sample_weight"

# 部署域 6 类（control_result==NONE 时数据中恰好只出现这 6 类，契约测试锁定）
DEPLOY_CLASSES = frozenset(
    {
        "CONTINUE_CURRENT", "SWITCH_NEW", "ACCEPT_NEW_INTENT",
        "SEND_TO_LLM", "ASK_CLARIFICATION", "UNKNOWN",
    }
)

# 决策错误代价（(真值, 预测) → 权重；未列出的错误默认 1，正确为 0）
_SWITCHY = ("SWITCH_NEW", "ACCEPT_NEW_INTENT")
COST_MATRIX: dict[tuple[str, str], float] = {
    **{("CONTINUE_CURRENT", p): 5.0 for p in _SWITCHY},   # 误切吞任务上下文
    **{(t, "CONTINUE_CURRENT"): 3.0 for t in _SWITCHY},   # 误吞新诉求
    **{("SEND_TO_LLM", p): 2.0 for p in DEPLOY_CLASSES if p != "SEND_TO_LLM"},
    **{(t, "SEND_TO_LLM"): 1.0 for t in DEPLOY_CLASSES if t != "SEND_TO_LLM"},
}

SEED = 42


# ---------------------------------------------------------------------------
# 数据加载与特征构造
# ---------------------------------------------------------------------------

def load_rows(path: Path, scope: str) -> list[dict[str, str]]:
    """读 CSV（UTF-8 BOM），deploy 域只保留 control_result==NONE 的行。"""
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # 特征契约断言：黑名单与白名单互斥、白名单列必须存在
    overlap = FORBIDDEN_COLUMNS & set(ALL_FEATURES)
    assert not overlap, f"泄漏黑名单混入特征白名单: {overlap}"
    missing = [c for c in ALL_FEATURES if c not in rows[0]]
    assert not missing, f"数据缺少白名单特征列: {missing}"
    if scope == "deploy":
        rows = [r for r in rows if r["control_result"] == "NONE"]
    return rows


def build_frame(rows: list[dict[str, str]]):
    """构造 (X: DataFrame, y: list, w: list)。类别列用 pandas category dtype
    （LightGBM/XGBoost 原生消费）；布尔转 0/1；数值转 float。"""
    import pandas as pd

    data: dict[str, list[Any]] = {}
    for col in CATEGORICAL_FEATURES:
        data[col] = [r[col] or "NONE" for r in rows]
    for col in BOOLEAN_FEATURES:
        data[col] = [1 if r[col] == "True" else 0 for r in rows]
    for col in NUMERIC_FEATURES:
        data[col] = [float(r[col] or 0.0) for r in rows]
    x = pd.DataFrame(data)
    for col in CATEGORICAL_FEATURES:
        x[col] = x[col].astype("category")
    y = [r[LABEL_COLUMN] for r in rows]
    w = [float(r.get(WEIGHT_COLUMN) or 1.0) for r in rows]
    return x, y, w


def align_categories(frames: list) -> None:
    """三个 split 的类别取值对齐（union），保证编码一致。"""
    for col in CATEGORICAL_FEATURES:
        cats = sorted({c for f in frames for c in f[col].cat.categories})
        for f in frames:
            f[col] = f[col].cat.set_categories(cats)


# ---------------------------------------------------------------------------
# 业务指标（不只看 Accuracy，见操作文档第 4 节）
# ---------------------------------------------------------------------------

def business_metrics(y_true: list[str], y_pred: list[str]) -> dict[str, float]:
    """代价敏感业务指标。分母为 0 时该项记 0.0。"""

    def rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    pairs = list(zip(y_true, y_pred))
    cont = [(t, p) for t, p in pairs if t == "CONTINUE_CURRENT"]
    switchy = [(t, p) for t, p in pairs if t in _SWITCHY]
    llm = [(t, p) for t, p in pairs if t == "SEND_TO_LLM"]
    non_llm = [(t, p) for t, p in pairs if t != "SEND_TO_LLM"]
    total_cost = sum(
        0.0 if t == p else COST_MATRIX.get((t, p), 1.0) for t, p in pairs
    )
    return {
        "false_switch_rate": rate(sum(1 for _, p in cont if p in _SWITCHY), len(cont)),
        "false_continue_rate": rate(
            sum(1 for _, p in switchy if p == "CONTINUE_CURRENT"), len(switchy)
        ),
        "llm_miss_rate": rate(sum(1 for _, p in llm if p != "SEND_TO_LLM"), len(llm)),
        "unnecessary_llm_rate": rate(
            sum(1 for _, p in non_llm if p == "SEND_TO_LLM"), len(non_llm)
        ),
        "clarification_rate": rate(
            sum(1 for _, p in pairs if p == "ASK_CLARIFICATION"), len(pairs)
        ),
        "cost_weighted_error": round(total_cost / len(pairs), 4) if pairs else 0.0,
    }


# ---------------------------------------------------------------------------
# 各模型训练（每个返回 (model, y_pred_test)；依赖缺失返回 None 由主流程跳过）
# ---------------------------------------------------------------------------

def _sklearn_preprocessor(scale: bool):
    from sklearn.compose import ColumnTransformer
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    num_cols = BOOLEAN_FEATURES + NUMERIC_FEATURES
    return ColumnTransformer(
        [
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", StandardScaler() if scale else "passthrough", num_cols),
        ]
    )


def train_lr(xt, yt, wt, xv, yv, xs):
    """Logistic Regression 基线：树模型赢不了它 ⇒ 特征/数据不够。"""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    model = Pipeline(
        [
            ("prep", _sklearn_preprocessor(scale=True)),
            ("clf", LogisticRegression(max_iter=3000, random_state=SEED)),
        ]
    )
    model.fit(xt, yt, clf__sample_weight=wt)
    return model, list(model.predict(xs))


def train_tree(xt, yt, wt, xv, yv, xs, out_dir: Path):
    """浅决策树：规则发现工具（导出文本规则），不上线。"""
    from sklearn.pipeline import Pipeline
    from sklearn.tree import DecisionTreeClassifier, export_text

    model = Pipeline(
        [
            ("prep", _sklearn_preprocessor(scale=False)),
            ("clf", DecisionTreeClassifier(max_depth=4, min_samples_leaf=30, random_state=SEED)),
        ]
    )
    model.fit(xt, yt, clf__sample_weight=wt)
    names = list(model.named_steps["prep"].get_feature_names_out())
    clf = model.named_steps["clf"]
    rules = export_text(
        clf, feature_names=names, class_names=[str(c) for c in clf.classes_]
    )
    (out_dir / "tree_rules.txt").write_text(rules, encoding="utf-8")
    print("—— 浅决策树规则（前 20 行，规则发现用）——")
    print("\n".join(rules.splitlines()[:20]))
    return model, list(model.predict(xs))


def train_lgbm(xt, yt, wt, xv, yv, xs):
    """LightGBM 主力候选：原生类别特征 + 早停 + 保守容量（防背合成规律）。"""
    try:
        import lightgbm as lgb
    except ImportError:
        print("[skip] lightgbm 未安装（uv sync --group train）")
        return None
    model = lgb.LGBMClassifier(
        n_estimators=600, learning_rate=0.05, num_leaves=31,
        min_child_samples=30, subsample=0.9, colsample_bytree=0.9,
        random_state=SEED, verbose=-1,
    )
    model.fit(
        xt, yt, sample_weight=wt, eval_set=[(xv, yv)],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    _print_importance("LightGBM", ALL_FEATURES, model.feature_importances_)
    return model, list(model.predict(xs))


def train_xgb(xt, yt, wt, xv, yv, xs):
    """XGBoost 对照：同拆分同指标。"""
    try:
        from xgboost import XGBClassifier
    except ImportError:
        print("[skip] xgboost 未安装（uv sync --group train）")
        return None
    from sklearn.preprocessing import LabelEncoder

    enc = LabelEncoder().fit(yt)
    model = XGBClassifier(
        n_estimators=600, learning_rate=0.05, max_depth=6,
        subsample=0.9, colsample_bytree=0.9, tree_method="hist",
        enable_categorical=True, random_state=SEED,
        early_stopping_rounds=50, eval_metric="mlogloss",
    )
    model.fit(
        xt, enc.transform(yt), sample_weight=wt,
        eval_set=[(xv, enc.transform(yv))], verbose=False,
    )
    _print_importance("XGBoost", ALL_FEATURES, model.feature_importances_)
    preds = enc.inverse_transform(model.predict(xs))
    return (model, enc), list(preds)


def _print_importance(name: str, cols: list[str], imps) -> None:
    top = sorted(zip(cols, imps), key=lambda x: -float(x[1]))[:8]
    print(f"—— {name} 特征重要性 Top8 ——")
    for c, v in top:
        print(f"  {c:28s} {float(v):.4f}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Meta-classifier 训练（Stage 27 v1）")
    parser.add_argument(
        "--data", default="data/电商客服_MetaClassifier_合成训练数据_v1.csv"
    )
    parser.add_argument("--out", default="models/meta_classifier_v1")
    parser.add_argument(
        "--scope", choices=["deploy", "full"], default="deploy",
        help="deploy=control_result==NONE 6 类（默认）；full=11 类诊断",
    )
    parser.add_argument("--models", default="lr,tree,lgbm,xgb")
    args = parser.parse_args()

    from sklearn.metrics import accuracy_score, classification_report, f1_score

    import joblib

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows(Path(args.data), args.scope)
    splits = {
        s: [r for r in rows if r["split"] == s] for s in ("train", "validation", "test")
    }
    print(
        f"scope={args.scope} rows={len(rows)} "
        f"(train={len(splits['train'])}/val={len(splits['validation'])}/test={len(splits['test'])})"
    )
    if args.scope == "deploy":
        labels = {r[LABEL_COLUMN] for r in rows}
        assert labels <= DEPLOY_CLASSES, f"部署域出现意外标签: {labels - DEPLOY_CLASSES}"

    xt, yt, wt = build_frame(splits["train"])
    xv, yv, _ = build_frame(splits["validation"])
    xs, ys, _ = build_frame(splits["test"])
    align_categories([xt, xv, xs])

    wanted = [m.strip() for m in args.models.split(",") if m.strip()]
    results: dict[str, dict[str, Any]] = {}
    for name in wanted:
        start = time.time()
        if name == "lr":
            trained = train_lr(xt, yt, wt, xv, yv, xs)
        elif name == "tree":
            trained = train_tree(xt, yt, wt, xv, yv, xs, out_dir)
        elif name == "lgbm":
            trained = train_lgbm(xt, yt, wt, xv, yv, xs)
        elif name == "xgb":
            trained = train_xgb(xt, yt, wt, xv, yv, xs)
        else:
            print(f"[skip] 未知模型 {name}")
            continue
        if trained is None:
            continue
        model, preds = trained
        metrics = {
            "accuracy": round(accuracy_score(ys, preds), 4),
            "macro_f1": round(f1_score(ys, preds, average="macro"), 4),
            **business_metrics(ys, preds),
            "per_class_f1": {
                k: round(v["f1-score"], 4)
                for k, v in classification_report(
                    ys, preds, output_dict=True, zero_division=0
                ).items()
                if isinstance(v, dict) and k not in ("macro avg", "weighted avg")
            },
            "train_seconds": round(time.time() - start, 1),
        }
        results[name] = metrics
        joblib.dump(model, out_dir / f"model_{name}.joblib")

    # —— 对比表 ——
    cols = [
        "accuracy", "macro_f1", "cost_weighted_error", "false_switch_rate",
        "false_continue_rate", "llm_miss_rate", "unnecessary_llm_rate",
    ]
    print("\n—— test 集对比（冠军=代价加权错误最低，macro-F1 平手裁决）——")
    print(f"{'model':6s} " + " ".join(f"{c:>20s}" for c in cols))
    for name, m in results.items():
        print(f"{name:6s} " + " ".join(f"{m[c]:>20.4f}" for c in cols))

    champion = min(
        results, key=lambda n: (results[n]["cost_weighted_error"], -results[n]["macro_f1"])
    ) if results else None
    print(f"\n冠军: {champion}（合成数据结论，不构成上线依据——见操作文档第 4 节）")

    # —— 产物：特征契约 + 指标 ——
    spec = {
        "scope": args.scope,
        "data": args.data,
        "label_classes": sorted({*yt, *yv, *ys}),
        "categorical": {c: list(xt[c].cat.categories) for c in CATEGORICAL_FEATURES},
        "boolean": BOOLEAN_FEATURES,
        "numeric": NUMERIC_FEATURES,
        "forbidden": sorted(FORBIDDEN_COLUMNS),
    }
    (out_dir / "feature_spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(
            {"champion": champion, "results": results}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print(f"产物已写入 {out_dir}/（model_*.joblib / feature_spec.json / metrics.json）")


if __name__ == "__main__":
    main()
