"""Stage 18 A/B 实验框架单测：确定性分桶 / 变体注入 / 白名单 / 降级。

不依赖真实流量（显著性结论由真实流量给），只验证分流与注入逻辑的确定性与安全性。
"""

import json

import pytest

from app.core.config import settings
from app.experiments import config as exp_config
from app.experiments.config import (
    OVERRIDABLE_PARAMS,
    load_experiments,
)
from app.experiments.resolver import (
    bucket_of,
    effective,
    resolve_experiment,
    set_overrides,
)


@pytest.fixture(autouse=True)
def _reset():
    """每例前后清配置缓存 + 清参数覆盖，避免串扰。"""
    exp_config.clear_cache()
    set_overrides({})
    yield
    exp_config.clear_cache()
    set_overrides({})


def _write_config(tmp_path, monkeypatch, payload: dict) -> str:
    path = tmp_path / "experiments.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(settings, "EXPERIMENTS_CONFIG_PATH", str(path))
    exp_config.clear_cache()
    return str(path)


def _fifty_fifty(param="RAG_MIN_SCORE", value=0.4, **scope):
    return {
        "experiments": [
            {
                "id": "exp_rag",
                "status": "running",
                "scope": scope,
                "variants": [
                    {"name": "control", "weight": 50, "params": {}},
                    {"name": "variant", "weight": 50, "params": {param: value}},
                ],
            }
        ]
    }


# ---------------- 确定性分桶 ----------------


def test_bucket_is_deterministic():
    b1 = bucket_of("exp", "t1", "s1")
    b2 = bucket_of("exp", "t1", "s1")
    assert b1 == b2
    assert 0 <= b1 < 100


def test_bucket_varies_by_inputs():
    # 不同会话大概率落不同桶（不强制不等，只验证不是常量）
    buckets = {bucket_of("exp", "t1", f"s{i}") for i in range(50)}
    assert len(buckets) > 5


def test_bucket_known_value_stable():
    """锚定一个已知值，防哈希实现被无意改动（跨进程一致的隐性契约）。"""
    import hashlib

    expected = int(hashlib.md5(b"exp:t1:s1").hexdigest(), 16) % 100
    assert bucket_of("exp", "t1", "s1") == expected


# ---------------- 变体解析与分流 ----------------


def test_no_config_returns_control(monkeypatch):
    monkeypatch.setattr(settings, "EXPERIMENTS_CONFIG_PATH", "")
    res = resolve_experiment("t1", "s1")
    assert res.assignments == []
    assert res.overrides == {}
    assert res.to_log() is None


def test_same_session_stable_variant(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, _fifty_fifty())
    first = resolve_experiment("t1", "s-stable")
    for _ in range(5):
        again = resolve_experiment("t1", "s-stable")
        assert again.assignments == first.assignments


def test_split_distribution_roughly_balanced(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, _fifty_fifty())
    counts = {"control": 0, "variant": 0}
    for i in range(1000):
        res = resolve_experiment("t1", f"sess-{i}")
        counts[res.assignments[0]["variant"]] += 1
    # 50/50 分流：两侧都应落在合理区间（md5 均匀性）
    assert 400 < counts["control"] < 600
    assert 400 < counts["variant"] < 600


def test_variant_injects_whitelisted_override(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, _fifty_fifty(value=0.33))
    # 找一个落到 variant 的会话
    hit = next(
        s
        for s in (f"s{i}" for i in range(200))
        if resolve_experiment("t1", s).assignments[0]["variant"] == "variant"
    )
    res = resolve_experiment("t1", hit)
    assert res.overrides.get("RAG_MIN_SCORE") == 0.33
    log = res.to_log()
    assert log is not None
    assert log["assignments"][0] == {"exp_id": "exp_rag", "variant": "variant"}


# ---------------- 作用域 ----------------


def test_tenant_scope_excludes_others(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, _fifty_fifty(tenants=["t-vip"]))
    assert resolve_experiment("t-vip", "s1").assignments  # 命中
    assert resolve_experiment("t-other", "s1").assignments == []  # 作用域外→control


@pytest.mark.parametrize("status", ["draft", "stopped", "paused"])
def test_non_running_status_is_control(tmp_path, monkeypatch, status):
    payload = _fifty_fifty()
    payload["experiments"][0]["status"] = status
    _write_config(tmp_path, monkeypatch, payload)
    assert resolve_experiment("t1", "s1").assignments == []


# ---------------- 白名单 ----------------


def test_non_whitelist_param_is_dropped(tmp_path, monkeypatch):
    payload = _fifty_fifty()
    # 变体夹带一个非白名单参数（企图偷改）→ 必须被丢弃
    payload["experiments"][0]["variants"][1]["params"] = {
        "RAG_MIN_SCORE": 0.4,
        "AUTH_ENABLED": False,  # 非白名单，危险项
        "OPENAI_API_KEY": "leak",  # 非白名单
    }
    _write_config(tmp_path, monkeypatch, payload)
    hit = next(
        s
        for s in (f"s{i}" for i in range(200))
        if resolve_experiment("t1", s).assignments[0]["variant"] == "variant"
    )
    ov = resolve_experiment("t1", hit).overrides
    assert ov == {"RAG_MIN_SCORE": 0.4}
    assert "AUTH_ENABLED" not in ov
    assert "OPENAI_API_KEY" not in ov


def test_whitelist_contents():
    assert "RAG_MIN_SCORE" in OVERRIDABLE_PARAMS
    assert "FAQ_HIT_THRESHOLD" in OVERRIDABLE_PARAMS
    assert "RERANKER_PROVIDER" in OVERRIDABLE_PARAMS
    # 安全项绝不可实验
    assert "AUTH_ENABLED" not in OVERRIDABLE_PARAMS
    assert "OPENAI_API_KEY" not in OVERRIDABLE_PARAMS


# ---------------- effective 参数注入 ----------------


def test_effective_falls_back_to_settings(monkeypatch):
    monkeypatch.setattr(settings, "RAG_MIN_SCORE", 0.6)
    set_overrides({})
    assert effective("RAG_MIN_SCORE") == 0.6


def test_effective_uses_override(monkeypatch):
    monkeypatch.setattr(settings, "RAG_MIN_SCORE", 0.6)
    set_overrides({"RAG_MIN_SCORE": 0.2})
    assert effective("RAG_MIN_SCORE") == 0.2
    set_overrides({})
    assert effective("RAG_MIN_SCORE") == 0.6  # 清空后回落


def test_effective_unknown_returns_default():
    assert effective("NOT_A_SETTING", "dflt") == "dflt"


# ---------------- 降级（fail-open）----------------


def test_missing_file_fail_open(monkeypatch):
    monkeypatch.setattr(settings, "EXPERIMENTS_CONFIG_PATH", "/no/such/experiments.json")
    exp_config.clear_cache()
    assert load_experiments() == []
    assert resolve_experiment("t1", "s1").assignments == []


def test_corrupt_json_fail_open(tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(settings, "EXPERIMENTS_CONFIG_PATH", str(path))
    exp_config.clear_cache()
    assert load_experiments() == []
    assert resolve_experiment("t1", "s1").assignments == []


def test_experiment_missing_id_or_variants_dropped(tmp_path, monkeypatch):
    payload = {
        "experiments": [
            {"status": "running", "variants": [{"name": "c", "weight": 1}]},  # 无 id
            {"id": "no_variants", "status": "running", "variants": []},  # 无变体
        ]
    }
    _write_config(tmp_path, monkeypatch, payload)
    assert load_experiments() == []


def test_config_reload_on_mtime_change(tmp_path, monkeypatch):
    path = _write_config(tmp_path, monkeypatch, _fifty_fifty())
    assert len(load_experiments()) == 1
    # 改成 stopped 并重写文件 → 重新加载（mtime 变）
    import os
    import time

    payload = _fifty_fifty()
    payload["experiments"][0]["status"] = "stopped"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    # 保证 mtime 前进（部分文件系统精度较粗）
    os.utime(path, (time.time() + 1, time.time() + 1))
    exps = load_experiments()
    assert exps[0].status == "stopped"
