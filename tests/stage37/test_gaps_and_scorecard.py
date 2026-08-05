"""Stage 37 知识缺口发现 + 规则版 Scorecard 回归测试（纯函数为主）。"""

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gaps = _load("export_kb_gaps")
scorecard = _load("score_sessions")


# ---------------- 缺口聚类 ----------------


def test_cluster_gaps_groups_same_topic():
    rows = [
        {"text": "海外退货运费谁出", "mode": "unknown_fallback"},
        {"text": "海外退货的运费怎么算", "mode": "rag_refused"},
        {"text": "海外地区退货运费多少", "mode": "unknown_fallback"},
        {"text": "发票抬头能改吗", "mode": "unknown_fallback"},  # 单次不足 min_count
    ]
    result = gaps.cluster_gaps(rows, min_count=3)
    assert len(result) == 1
    gap = result[0]
    assert gap["sample_count"] == 3
    assert "rag_refused:1" in gap["failure_modes"]
    assert "unknown_fallback:2" in gap["failure_modes"]
    assert gap["candidate_questions"].count("/") == 2  # 3 条示例


def test_cluster_gaps_empty_and_threshold():
    assert gaps.cluster_gaps([], 3) == []
    assert gaps.cluster_gaps([{"text": "只出现一次", "mode": "x"}], 3) == []
    assert gaps.cluster_gaps([{"text": "", "mode": "x"}] * 5, 3) == []  # 空文本忽略


def test_overlap_clustering_tolerates_segmentation():
    """分词边界不稳（「退货运费」→「货运费」）时同话题仍聚一桶（重叠并桶）。"""
    rows = [
        {"text": "海外退货运费谁出", "mode": "unknown_fallback"},
        {"text": "海外退货的运费谁出", "mode": "unknown_fallback"},
        {"text": "海外地区退货运费多少", "mode": "unknown_fallback"},
    ]
    result = gaps.cluster_gaps(rows, min_count=3)
    assert len(result) == 1 and result[0]["sample_count"] == 3


# ---------------- Scorecard ----------------


def _turn(status="DONE", pred="ORDER.QUERY_STATUS", trace=None, proactive=False):
    return {"status": status, "pred_label": pred,
            "trace": trace or [], "proactive_applied": proactive}


def test_score_perfect_session():
    result = scorecard.score_session([_turn(), _turn()])
    assert result["auto_score"] == 100 and result["resolved"] == 1
    assert result["human_score"] == ""  # 三标签：人工位留空


def test_score_unresolved_handoff():
    result = scorecard.score_session([_turn(status="HANDOFF")])
    assert result["handoff"] == 1 and result["resolved"] == 0
    assert result["auto_score"] == 70  # -30 未解决


def test_score_unknown_and_corrections_capped():
    turns = [_turn(status="FALLBACK", pred="META.UNKNOWN")] * 5 + [
        _turn(trace=["response_generate:switch_guard"]),
        _turn(trace=["response_generate:unknown_hold"]),
        _turn(trace=["response_generate:task_denied"]),
        _turn(trace=["response_generate:switch_guard"]),
    ]
    result = scorecard.score_session(turns)
    assert result["unknown_turns"] == 5
    assert result["correction_turns"] == 4
    # 扣分封顶：UNKNOWN -24、纠偏 -18（有 DONE 轮=已解决不扣 30）
    assert result["auto_score"] == 100 - 24 - 18


def test_score_marketing_and_user_signal():
    turns = [_turn(proactive=True), _turn(proactive=True), _turn(proactive=True)]
    result = scorecard.score_session(turns, csat=2)
    # 营销 3 次：超出 1 次的部分 -5×2；低分 CSAT -20
    assert result["marketing_applied"] == 3
    assert result["auto_score"] == 100 - 10 - 20
    assert result["user_score"] == 2


def test_score_floor_zero():
    turns = [_turn(status="FALLBACK", pred="META.UNKNOWN")] * 10
    result = scorecard.score_session(turns, csat=1, feedback_down=2)
    assert result["auto_score"] >= 0
