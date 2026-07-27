"""Stage 25 监控告警测试：指标基数整改 + 配置文件有效性与指标名交叉校验。"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MON = ROOT / "deploy" / "monitoring"


def _defined_metric_names() -> set[str]:
    """从 metrics.py 源码提取全部指标名（Counter/Histogram 第一个参数）。"""
    src = (ROOT / "app" / "core" / "metrics.py").read_text()
    names = set(re.findall(r'(?:Counter|Histogram)\(\s*"([a-z0-9_]+)"', src))
    assert names, "metrics.py 未解析出任何指标名"
    # Histogram 自动派生 _bucket/_count/_sum
    derived = set()
    for name in names:
        derived |= {name, f"{name}_bucket", f"{name}_count", f"{name}_sum"}
    return derived


def _referenced_metrics(text: str) -> set[str]:
    """从 PromQL/看板表达式中提取本项目指标名（约定前缀集合）。"""
    return set(
        re.findall(
            r"\b((?:chat|kb|llm|intent|rag|semantic|diagnose|direction|confirm"
            r"|action|handoff|rate_limited|guardrail)[a-z0-9_]*)\b",
            text,
        )
    )


# ---------------- 指标基数整改 ----------------


def test_llm_metrics_have_no_tenant_label():
    """llm_tokens/budget_exceeded 不再带 tenant label（多租户不进 label 原则）。"""
    from app.core.metrics import LLM_BUDGET_EXCEEDED, LLM_TOKENS

    assert "tenant" not in LLM_TOKENS._labelnames
    assert LLM_TOKENS._labelnames == ("purpose",)
    assert LLM_BUDGET_EXCEEDED._labelnames == ()


def test_no_tenant_label_anywhere():
    """全部指标声明中不允许出现 tenant label（防回潮）。"""
    src = (ROOT / "app" / "core" / "metrics.py").read_text()
    for match in re.finditer(r"\[([^\]]*)\]", src):
        assert '"tenant"' not in match.group(1), "指标 label 出现 tenant（高基数违规）"


# ---------------- 配置有效性与指标名交叉校验 ----------------


def test_prometheus_and_alerts_yaml_valid():
    prom = yaml.safe_load((MON / "prometheus.yml").read_text())
    assert any(sc["job_name"] == "api" for sc in prom["scrape_configs"])
    assert "/etc/prometheus/alerts.yml" in prom["rule_files"]

    alerts = yaml.safe_load((MON / "alerts.yml").read_text())
    rules = alerts["groups"][0]["rules"]
    assert len(rules) >= 8
    defined = _defined_metric_names()
    for rule in rules:
        assert rule["alert"] and rule["expr"] and rule["labels"]["severity"]
        for metric in _referenced_metrics(rule["expr"]):
            assert metric in defined, f"告警 {rule['alert']} 引用了不存在的指标 {metric}"


def test_dashboard_json_valid_and_metrics_exist():
    dashboard = json.loads(
        (MON / "grafana" / "dashboards" / "ai-cs-platform.json").read_text()
    )
    assert dashboard["uid"] == "ai-cs-platform"
    defined = _defined_metric_names()
    panel_count = 0
    for panel in dashboard["panels"]:
        panel_count += 1
        for target in panel["targets"]:
            for metric in _referenced_metrics(target["expr"]):
                assert metric in defined, f"面板「{panel['title']}」引用不存在的指标 {metric}"
    assert panel_count >= 10


def test_grafana_provisioning_valid():
    ds = yaml.safe_load(
        (MON / "grafana" / "provisioning" / "datasources" / "prometheus.yml").read_text()
    )
    assert ds["datasources"][0]["uid"] == "prometheus"
    provider = yaml.safe_load(
        (MON / "grafana" / "provisioning" / "dashboards" / "default.yml").read_text()
    )
    assert provider["providers"][0]["options"]["path"] == "/var/lib/grafana/dashboards"


def test_compose_monitoring_profile_and_cron_heartbeat():
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    services = compose["services"]
    assert "prometheus" in services and "grafana" in services
    assert services["prometheus"]["profiles"] == ["monitoring"]
    # Grafana 密码强制 .env 插值（零明文纪律）
    raw = (ROOT / "docker-compose.prod.yml").read_text()
    assert "GRAFANA_ADMIN_PASSWORD:?" in raw
    # cron 心跳 healthcheck 与调度器写入路径一致
    cron_check = " ".join(services["cron"]["healthcheck"]["test"])
    assert "scheduler-heartbeat" in cron_check
    scheduler_src = (ROOT / "deploy" / "scheduler.py").read_text()
    assert "scheduler-heartbeat" in scheduler_src


# ---------------- 调度器心跳 ----------------


def test_scheduler_writes_heartbeat(tmp_path, monkeypatch):
    import sys

    from deploy.scheduler import Job, loop

    hb = tmp_path / "hb"
    monkeypatch.setenv("SCHEDULER_HEARTBEAT_FILE", str(hb))
    job = Job(name="t", cmd=[sys.executable, "-c", "pass"], interval=60.0, retry_delay=0)
    loop([job], now=lambda: 0.0, sleep=lambda s: None, max_ticks=1)
    assert hb.exists() and float(hb.read_text()) > 0
