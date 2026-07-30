"""Stage 27 Meta-classifier 契约回归测试。

锁定三件事（换数据版本 v2 时先跑这里）：
1. 特征契约：泄漏黑名单与白名单互斥、关键泄漏列在黑名单内；
2. 数据契约：部署域恰好 6 类、split 组安全（case_family 零跨集）；
3. 业务指标计算正确（代价加权/各分项率）。

训练脚本在 scripts/（非包），经 importlib 按路径加载；
pandas 属 train 依赖组，相关用例在未安装环境自动跳过。
"""

import csv
import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DATA = _ROOT / "data" / "电商客服_MetaClassifier_合成训练数据_v1.csv"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "train_meta_classifier", _ROOT / "scripts" / "train_meta_classifier.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mc = _load_script()


# ---------------- 特征契约 ----------------


def test_forbidden_and_features_disjoint():
    assert not (mc.FORBIDDEN_COLUMNS & set(mc.ALL_FEATURES))


def test_known_leaky_columns_are_forbidden():
    """实证泄漏列必须在黑名单（llm_review_expected 与 SEND_TO_LLM 100% 同向；
    message 单条重复至 200 次；target_* 是标签侧）。"""
    for col in (
        "llm_review_expected", "message", "control_result",
        "target_decision", "target_intent", "sample_weight",
        "row_id", "case_family_id", "scenario_family", "split", "notes",
    ):
        assert col in mc.FORBIDDEN_COLUMNS, col


def test_feature_columns_exist_in_data():
    header = next(csv.reader(_DATA.open(encoding="utf-8-sig")))
    for col in mc.ALL_FEATURES:
        assert col in header, col


# ---------------- 数据契约 ----------------


@pytest.fixture(scope="module")
def rows():
    return list(csv.DictReader(_DATA.open(encoding="utf-8-sig")))


def test_deploy_scope_exactly_six_classes(rows):
    deploy = [r for r in rows if r["control_result"] == "NONE"]
    labels = {r["target_decision"] for r in deploy}
    assert labels == set(mc.DEPLOY_CLASSES)


def test_rule_scope_never_reaches_meta(rows):
    """非 NONE 行的标签全是规则域决策（运行时被 classify_control 短路）。"""
    rule_labels = {
        r["target_decision"] for r in rows if r["control_result"] != "NONE"
    }
    # ACCEPT_NEW_INTENT 例外：RULE_KEYWORD 命中业务意图（如取消订单正则）
    assert rule_labels <= {
        "CONFIRM_CURRENT", "DENY_CURRENT", "CANCEL_CURRENT", "CORRECT_CURRENT",
        "TRANSFER_HUMAN", "ACCEPT_NEW_INTENT", "CONTINUE_CURRENT",
    }


def test_split_is_group_safe(rows):
    """case_family 不得跨 split（防近重复泄漏——同族样本是同话术扰动生成）。"""
    fam_split: dict[str, str] = {}
    for r in rows:
        fam = r["case_family_id"]
        assert fam_split.setdefault(fam, r["split"]) == r["split"], fam


def test_all_splits_present(rows):
    assert {r["split"] for r in rows} == {"train", "validation", "test"}


# ---------------- 业务指标 ----------------


def test_business_metrics_computation():
    y_true = ["CONTINUE_CURRENT", "CONTINUE_CURRENT", "SWITCH_NEW", "SEND_TO_LLM"]
    y_pred = ["SWITCH_NEW", "CONTINUE_CURRENT", "CONTINUE_CURRENT", "SWITCH_NEW"]
    m = mc.business_metrics(y_true, y_pred)
    assert m["false_switch_rate"] == 0.5  # 2 条 CONTINUE 中 1 条误切
    assert m["false_continue_rate"] == 1.0  # 1 条 SWITCH 全误吞
    assert m["llm_miss_rate"] == 1.0
    assert m["unnecessary_llm_rate"] == 0.0
    # 代价：误切 5 + 误吞 3 + 漏二判 2，共 10 / 4 条
    assert m["cost_weighted_error"] == 2.5


def test_business_metrics_perfect_prediction():
    y = ["CONTINUE_CURRENT", "SEND_TO_LLM", "UNKNOWN"]
    m = mc.business_metrics(y, list(y))
    assert m["cost_weighted_error"] == 0.0
    assert m["false_switch_rate"] == 0.0


# ---------------- 特征构造（需 pandas，未装 train 组自动跳过） ----------------


def test_build_frame_smoke(rows):
    pytest.importorskip("pandas")
    deploy = [r for r in rows if r["control_result"] == "NONE"][:200]
    x, y, w = mc.build_frame(deploy)
    assert list(x.columns) == mc.ALL_FEATURES
    assert len(x) == len(y) == len(w) == 200
    # 黑名单列不得出现在特征帧
    assert not (set(x.columns) & mc.FORBIDDEN_COLUMNS)
    # 布尔已转 0/1、数值为 float
    assert set(x["slot_match"].unique()) <= {0, 1}
    assert x["setfit_margin"].dtype.kind == "f"
