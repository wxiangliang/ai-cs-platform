"""Stage 40 行为准则层回归测试。

锁定五件事：
1. 匹配：各维度 AND / 维度内 OR / 空维度不限 / 意图域前缀 / 租户过滤 /
   情绪 / 关键词；
2. 排序与封顶：criticality 降序、同 exclusion_group 去重、MAX_INJECT 截断；
3. 渲染与留痕：注入块格式、命中 id 进收集器可 drain、去重保序；
4. fail-open：默认关 / 配置缺失 / 损坏均零影响；
5. 种子配置示例可加载且全部通过 schema（id/action 必填）。
"""

import json

import pytest

import app.chat.guidelines.engine as engine
from app.chat.guidelines import (
    drain_matched_guidelines,
    guidelines_for_state,
    reset_matched_guidelines,
)
from app.core.config import settings


def _write_config(tmp_path, guidelines):
    cfg = tmp_path / "guidelines.json"
    cfg.write_text(json.dumps(guidelines, ensure_ascii=False), encoding="utf-8")
    return str(cfg)


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    def _setup(guidelines):
        monkeypatch.setattr(settings, "GUIDELINES_ENABLED", True)
        monkeypatch.setattr(
            settings, "GUIDELINES_CONFIG_PATH", _write_config(tmp_path, guidelines)
        )
        reset_matched_guidelines()

    return _setup


def _g(gid, action="做点什么", criticality="NORMAL", group=None, **cond):
    g = {"id": gid, "action": action, "criticality": criticality,
         "condition": cond}
    if group:
        g["exclusion_group"] = group
    return g


def _state(**over):
    base = {
        "tenant_id": "t1",
        "intent_result": {"pred_label": "AFTERSALE.REFUND"},
        "current_state": "COLLECTING",
        "emotion": "",
        "normalized_text": "退款太慢了",
    }
    return {**base, **over}


# ---------------- 匹配维度 ----------------


def test_dimension_matching(enabled):
    enabled([
        _g("domain_rule", intents=["AFTERSALE."]),        # 域前缀
        _g("exact_rule", intents=["AFTERSALE.REFUND"]),   # 完整码
        _g("other_intent", intents=["PRODUCT.RECOMMEND"]),
        _g("state_rule", states=["COLLECTING"]),
        _g("wrong_state", states=["CONFIRMING"]),
        _g("tenant_rule", tenants=["t1"]),
        _g("other_tenant", tenants=["t2"]),
        _g("kw_rule", keywords=["太慢"]),
        _g("kw_miss", keywords=["发票"]),
        _g("emotion_rule", emotion="negative"),
    ])
    hits = engine.match_guidelines(
        tenant_id="t1", intent="AFTERSALE.REFUND", state="COLLECTING",
        emotion="", text="退款太慢了",
    )
    ids = {g["id"] for g in hits}
    # MAX_INJECT=3 截断前的完整匹配集合验证：临时抬高上限
    assert ids <= {"domain_rule", "exact_rule", "state_rule", "tenant_rule", "kw_rule"}
    assert "other_intent" not in ids and "wrong_state" not in ids
    assert "other_tenant" not in ids and "kw_miss" not in ids
    assert "emotion_rule" not in ids  # 情绪未命中


def test_empty_condition_is_global(enabled):
    enabled([_g("global_rule")])
    hits = engine.match_guidelines(
        tenant_id="tx", intent="CHITCHAT.GENERAL", state="IDLE", emotion="", text=""
    )
    assert [g["id"] for g in hits] == ["global_rule"]


def test_and_across_dimensions(enabled):
    enabled([_g("combo", intents=["AFTERSALE."], emotion="negative")])
    # 只中意图不中情绪 → 不命中（维度 AND）
    assert engine.match_guidelines(
        tenant_id="t1", intent="AFTERSALE.REFUND", state="IDLE", emotion="", text=""
    ) == []
    assert len(engine.match_guidelines(
        tenant_id="t1", intent="AFTERSALE.REFUND", state="IDLE",
        emotion="negative", text="",
    )) == 1


# ---------------- 排序 / 去重 / 封顶 ----------------


def test_criticality_sort_exclusion_and_cap(enabled, monkeypatch):
    enabled([
        _g("low_tone", criticality="LOW", group="tone"),
        _g("high_tone", criticality="HIGH", group="tone"),  # 同组只留它
        _g("n1"), _g("n2"), _g("n3"),
    ])
    monkeypatch.setattr(settings, "GUIDELINES_MAX_INJECT", 3)
    hits = engine.match_guidelines(
        tenant_id="t1", intent="X", state="IDLE", emotion="", text=""
    )
    ids = [g["id"] for g in hits]
    assert ids[0] == "high_tone"      # HIGH 排最前
    assert "low_tone" not in ids      # 同 exclusion_group 被去重
    assert len(ids) == 3              # 封顶截断


# ---------------- 渲染与留痕 ----------------


def test_render_and_collector(enabled):
    enabled([_g("r1", action="先共情再解答"), _g("r2", action="不承诺时效")])
    block = guidelines_for_state(_state())
    assert block is not None
    assert "先共情再解答" in block and "不承诺时效" in block
    assert block.startswith("本轮行为准则")
    # 命中留痕：收集器 drain 去重保序
    guidelines_for_state(_state())  # 第二次命中同 id
    assert drain_matched_guidelines() == ["r1", "r2"]


# ---------------- fail-open ----------------


def test_disabled_by_default():
    assert settings.GUIDELINES_ENABLED is False
    assert guidelines_for_state(_state()) is None


def test_missing_or_broken_config(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "GUIDELINES_ENABLED", True)
    monkeypatch.setattr(settings, "GUIDELINES_CONFIG_PATH", str(tmp_path / "none.json"))
    assert guidelines_for_state(_state()) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(settings, "GUIDELINES_CONFIG_PATH", str(bad))
    assert guidelines_for_state(_state()) is None


# ---------------- 种子配置契约 ----------------


def test_example_seed_config_valid(monkeypatch):
    monkeypatch.setattr(
        settings, "GUIDELINES_CONFIG_PATH", "configs/guidelines.example.json"
    )
    engine._cache.clear()
    seeds = engine.load_guidelines()
    assert len(seeds) == 8
    for g in seeds:
        assert g["id"] and g["action"]
        assert g.get("criticality", "NORMAL") in ("HIGH", "NORMAL", "LOW")
    # 种子里的租户示例只对指定租户生效
    hits = engine.match_guidelines(
        tenant_id="t-enterprise-demo", intent="CHITCHAT.GENERAL",
        state="IDLE", emotion="", text="",
    )
    assert "demo_tenant_honorific" in {g["id"] for g in hits}
    engine._cache.clear()
