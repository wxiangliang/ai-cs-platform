"""Stage 27 影子模式测试：特征构造 / 部署域过滤 / 降级 / 对照口径 / 实预测。

实预测用例依赖本地训练产物（models/meta_classifier_v1/），缺失时自动跳过
（CI 无产物环境=验证降级路径本身）。
"""

from pathlib import Path

import pytest

from app.chat.intent import meta_shadow
from app.chat.intent.types import DecisionSource, IntentLabel
from app.core.config import settings

_ARTIFACTS = (
    Path(meta_shadow.__file__).resolve().parents[3]
    / settings.META_SHADOW_DIR
    / "feature_spec.json"
)


def _state(source=DecisionSource.SETFIT, **over):
    base = {
        "normalized_text": "帮我查下物流",
        "current_state": "COLLECTING",
        "active_task": {
            "intent": "AFTERSALE.REFUND",
            "required_slots": ["order_id"],
            "collected_slots": {},
        },
        "task_stack": [],
        "slots": {},
        "intent_result": {
            "pred_label": "LOGISTICS.TRACK",
            "confidence": 0.82,
            "decision_source": source,
            "top_k": [
                {"label": "LOGISTICS.TRACK", "score": 0.82},
                {"label": "ORDER.QUERY_STATUS", "score": 0.11},
            ],
            "margin": 0.71,
        },
    }
    base.update(over)
    return base


# ---------------- 特征构造 ----------------


def test_build_features_matches_contract():
    features = meta_shadow.build_features(_state(), _state()["intent_result"])
    # 与训练脚本白名单同构（键集合一致由 spec 用例锁定，这里查关键映射）
    assert features["current_state"] == "COLLECTING"
    assert features["active_domain"] == "AFTERSALE"
    assert features["pending_slot"] == "customer_phone_or_order_id"  # 取值域映射
    assert features["setfit_top1_label"] == "LOGISTICS.TRACK"
    assert features["setfit_margin"] == 0.71
    assert features["has_active_task"] == 1
    assert features["slot_match"] == 0


def test_build_features_keys_align_with_training_whitelist():
    import importlib.util

    root = Path(meta_shadow.__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "tmc", root / "scripts" / "train_meta_classifier.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    features = meta_shadow.build_features(_state(), _state()["intent_result"])
    assert set(features) == set(mod.ALL_FEATURES)


def test_pending_fill_maps_slot_match():
    s = _state()
    s["intent_result"]["pending_fill"] = {
        "slot": "order_id", "value": "12345678", "evidence": "explicit_slot_name"
    }
    features = meta_shadow.build_features(s, s["intent_result"])
    assert features["slot_match"] == 1
    assert features["slot_match_type"] == "EXPLICIT_SLOT_NAME"
    assert features["slot_value_type"] == "ORDER_ID_OR_PHONE"


# ---------------- 部署域与降级 ----------------


def test_rule_sources_out_of_scope():
    """控制层来源不做影子预测（训练域 control_result==NONE 对齐）。"""
    for src in (
        DecisionSource.RULE_KEYWORD, DecisionSource.RULE_CONFIRM_GATE,
        DecisionSource.RULE_SLOT_ONLY, DecisionSource.RULE_PENDING_SLOT,
        DecisionSource.RULE_TASK_DENY,
    ):
        assert meta_shadow.shadow_predict(_state(source=src), {}) is None


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "META_SHADOW_ENABLED", False)
    assert meta_shadow.shadow_predict(_state(), {}) is None


def test_missing_artifacts_still_collects_features(monkeypatch, tmp_path):
    """产物缺失：预测层停用但**特征采集照常**——真实训练数据回流不中断
    （models/ 不进镜像的部署形态下决策日志仍积累特征+弱标签）。"""
    monkeypatch.setattr(settings, "META_SHADOW_DIR", str(tmp_path / "nope"))
    meta_shadow.reset_for_test()
    try:
        record = meta_shadow.shadow_predict(_state(), {"switch_candidate": "X"})
        assert record is not None
        assert "features" in record and record["actual"] == "CONTINUE_CURRENT"
        assert "decision" not in record  # 无模型不预测
    finally:
        meta_shadow.reset_for_test()


# ---------------- 原因码派生（决策融合层评审采纳项） ----------------


def test_reason_codes_derivation():
    s = _state(source=DecisionSource.LLM)
    s["intent_result"]["example_knn"] = {"label": "ORDER.QUERY_STATUS", "similarity": 0.7}
    features = meta_shadow.build_features(s, s["intent_result"])
    codes = meta_shadow.derive_reason_codes(features, s["intent_result"])
    assert "llm_second_opinion" in codes
    assert "knn_disagrees_top1" in codes  # KNN 说 ORDER，top1 是 LOGISTICS
    assert "has_active_task" in codes
    assert "differs_from_active_task" in codes  # 任务是退款，top1 是物流


def test_reason_codes_low_margin_and_agreement():
    s = _state()
    s["intent_result"].update({"confidence": 0.78, "margin": 0.02})
    s["intent_result"]["top_k"] = [
        {"label": "LOGISTICS.TRACK", "score": 0.78},
        {"label": "ORDER.QUERY_STATUS", "score": 0.76},
    ]
    s["intent_result"]["example_knn"] = {"label": "LOGISTICS.TRACK", "similarity": 0.9}
    features = meta_shadow.build_features(s, s["intent_result"])
    codes = meta_shadow.derive_reason_codes(features, s["intent_result"])
    assert "low_margin" in codes
    assert "knn_agrees_top1" in codes


def test_reason_codes_in_shadow_record(monkeypatch, tmp_path):
    """原因码随影子记录落库（产物缺失也照采——与特征采集同层）。"""
    monkeypatch.setattr(settings, "META_SHADOW_DIR", str(tmp_path / "nope"))
    meta_shadow.reset_for_test()
    try:
        record = meta_shadow.shadow_predict(_state(), {"switch_candidate": "X"})
        assert record is not None
        assert isinstance(record["reason_codes"], list)
        assert "has_active_task" in record["reason_codes"]
    finally:
        meta_shadow.reset_for_test()


def test_knn_confirmed_source_in_shadow_scope():
    """KNN 确认来源是语义层决策，属影子部署域（口径修正）。"""
    assert DecisionSource.SETFIT_KNN_CONFIRMED in meta_shadow._SHADOW_SOURCES


# ---------------- 对照口径 ----------------


def test_map_actual_decision():
    s = _state()
    # 守护拦截 → hold
    assert meta_shadow.map_actual_decision(s, {"switch_candidate": "X"}) == "CONTINUE_CURRENT"
    # LLM 二判来源 → SEND_TO_LLM
    s2 = _state(source=DecisionSource.LLM)
    assert meta_shadow.map_actual_decision(s2, {}) == "SEND_TO_LLM"
    # UNKNOWN 无任务 → UNKNOWN
    s3 = _state()
    s3["active_task"] = None
    s3["intent_result"]["pred_label"] = IntentLabel.META_UNKNOWN
    assert meta_shadow.map_actual_decision(s3, {}) == "UNKNOWN"
    # 任务中切换成功 → SWITCH_NEW
    assert (
        meta_shadow.map_actual_decision(
            s, {"active_task": {"intent": "LOGISTICS.TRACK"}}
        )
        == "SWITCH_NEW"
    )
    # IDLE 新开 → ACCEPT_NEW_INTENT
    s4 = _state(current_state="IDLE")
    s4["active_task"] = None
    assert meta_shadow.map_actual_decision(s4, {}) == "ACCEPT_NEW_INTENT"


# ---------------- 实预测（需本地训练产物） ----------------


@pytest.mark.skipif(not _ARTIFACTS.exists(), reason="需先跑 train_meta_classifier.py")
def test_shadow_predict_live():
    meta_shadow.reset_for_test()
    result = meta_shadow.shadow_predict(_state(), {"active_task": {"intent": "LOGISTICS.TRACK"}})
    assert result is not None
    assert result["decision"] in {
        "CONTINUE_CURRENT", "SWITCH_NEW", "ACCEPT_NEW_INTENT",
        "SEND_TO_LLM", "ASK_CLARIFICATION", "UNKNOWN",
    }
    assert result["actual"] == "SWITCH_NEW"
    assert isinstance(result["agree"], bool)
    assert result["model"]
    # 特征向量随预测一并落库（真实训练数据回流通道）
    assert "features" in result


# ---------------- 训练数据回流导出（契约） ----------------


def _load_export_script():
    import importlib.util

    root = Path(meta_shadow.__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "export_meta", root / "scripts" / "export_meta_training_set.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_export_row_matches_training_contract():
    """导出行必须能被训练脚本 load_rows/build_frame 原样消费。"""
    ex = _load_export_script()
    features = meta_shadow.build_features(_state(), _state()["intent_result"])
    shadow = {"features": features, "actual": "SWITCH_NEW", "decision": "CONTINUE_CURRENT", "agree": False}
    row = ex.build_export_row(1, "s-001", "查下物流", shadow)
    assert row is not None
    # 特征列齐全且布尔写 "True"/"False"（训练侧按 =="True" 解析）
    for col in ex._FEATURE_COLUMNS:
        assert col in row
    assert row["has_active_task"] == "True"
    assert row["slot_match"] == "False"
    assert row["control_result"] == "NONE"
    assert row["target_decision"] == "SWITCH_NEW"
    assert row["sample_weight"] == 1.5  # 分歧样本加权（难例）
    assert row["case_family_id"] == "session:s-001"
    # 三列标签纪律：policy=链路事实（不可改）、target 初始=policy（弱标签）、
    # reviewed 留白待人工（维持原判也要显式填写，与未审核可区分）
    assert row["policy_decision"] == "SWITCH_NEW" == row["target_decision"]
    assert row["reviewed_decision"] == ""


def test_export_split_group_safe():
    """同会话必须同 split（组安全），且分桶确定可复现。"""
    ex = _load_export_script()
    for sid in ("s-001", "s-002", "abc"):
        assert ex._split_for_session(sid) == ex._split_for_session(sid)
        assert ex._split_for_session(sid) in {"train", "validation", "test"}


def test_hindsight_tier_grading():
    """后见信号证据分级：task_deny=强（用户明确纠错）＞低分/差评=中＞
    仅转人工=弱（归因不确定，绝不直接当「决策错误」真值）。"""
    ex = _load_export_script()
    assert ex.hindsight_tier("") == ""
    assert ex.hindsight_tier("task_deny") == "strong"
    assert ex.hindsight_tier("task_deny,handoff,low_csat,feedback_down") == "strong"
    assert ex.hindsight_tier("handoff,low_csat") == "medium"
    assert ex.hindsight_tier("feedback_down") == "medium"
    assert ex.hindsight_tier("handoff") == "weak"


def test_split_by_time_group_safe_and_chronological():
    """时间切分：整会话粒度（组安全）+ 时间序单调（旧会话 train、新会话 test）。"""
    ex = _load_export_script()
    sessions = [f"s-{i:03d}" for i in range(100)]
    mapping = ex._split_by_time(sessions)
    rank = {"train": 0, "validation": 1, "test": 2}
    order = [rank[mapping[s]] for s in sessions]
    assert order == sorted(order)  # 时间上单调：不会新数据混进 train
    assert order.count(0) == 80 and order.count(1) == 10 and order.count(2) == 10
    assert ex._split_by_time([]) == {}


def test_export_row_split_override():
    """时间切分模式下 build_export_row 用调用方 split，不落回 md5 分桶。"""
    ex = _load_export_script()
    features = meta_shadow.build_features(_state(), _state()["intent_result"])
    row = ex.build_export_row(
        1, "s-001", "m", {"features": features, "actual": "SWITCH_NEW"}, split="test"
    )
    assert row is not None
    assert row["split"] == "test"


def test_hindsight_signal_composition():
    """后见信号拼接：审核优先级的筛选依据（系统事后自证决策错误）。"""
    ex = _load_export_script()
    assert ex.hindsight_signal(False, False, False, False) == ""
    assert ex.hindsight_signal(True, False, False, False) == "task_deny"
    assert ex.hindsight_signal(True, True, True, True) == "task_deny,handoff,low_csat,feedback_down"


def test_export_row_carries_hindsight():
    ex = _load_export_script()
    features = meta_shadow.build_features(_state(), _state()["intent_result"])
    shadow = {"features": features, "actual": "SWITCH_NEW"}
    row = ex.build_export_row(1, "s-001", "m", shadow, hindsight="task_deny,low_csat")
    assert row is not None
    assert row["hindsight_signal"] == "task_deny,low_csat"
    # 后见信号不改 sample_weight（未审核标签不加权，纪律见脚本 docstring）
    assert row["sample_weight"] == 1.0


def test_export_feature_columns_match_training_whitelist():
    """导出脚本特征列 == 训练脚本白名单（防两处清单漂移）。"""
    import importlib.util

    ex = _load_export_script()
    root = Path(meta_shadow.__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "tmc2", root / "scripts" / "train_meta_classifier.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(ex._FEATURE_COLUMNS) == set(mod.ALL_FEATURES)
    assert set(ex._BOOL_COLUMNS) == set(mod.BOOLEAN_FEATURES)


def test_export_row_skips_incomplete_shadow():
    ex = _load_export_script()
    assert ex.build_export_row(1, "s", "m", {"actual": "UNKNOWN"}) is None  # 缺特征
    assert ex.build_export_row(1, "s", "m", {"features": {}}) is None
