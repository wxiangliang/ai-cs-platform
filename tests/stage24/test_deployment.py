"""Stage 24 部署编排测试：调度器行为 + 编排文件静态一致性。"""

import sys
from pathlib import Path

import yaml

from deploy.scheduler import Job, _env_interval, build_jobs, loop, run_job

ROOT = Path(__file__).resolve().parents[2]


def _job(cmd, retries=2, interval=60.0):
    return Job(name="t", cmd=cmd, interval=interval, retries=retries, retry_delay=0.01)


def _ok_cmd():
    return [sys.executable, "-c", "print('ok')"]


def _fail_cmd():
    return [sys.executable, "-c", "import sys; sys.exit(3)"]


# ---------------- 调度器：重试与告警 ----------------


def test_run_job_success_no_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr("deploy.scheduler._post_alert", alerts.append)
    assert run_job(_job(_ok_cmd()), sleep=lambda s: None) is True
    assert alerts == []


def test_run_job_retries_then_alerts(monkeypatch):
    alerts = []
    sleeps = []
    monkeypatch.setattr("deploy.scheduler._post_alert", alerts.append)
    assert run_job(_job(_fail_cmd(), retries=2), sleep=sleeps.append) is False
    assert len(sleeps) == 2  # 重试 2 次（线性递增退避）
    assert sleeps[1] > sleeps[0]
    assert len(alerts) == 1 and alerts[0]["job"] == "t" and alerts[0]["rc"] == 3


def test_alert_skipped_without_webhook(monkeypatch):
    """未配置 ALERT_WEBHOOK_URL → 不发起网络请求。"""
    from deploy.scheduler import _post_alert

    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)

    def _boom(*a, **k):
        raise AssertionError("不应发起网络请求")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    _post_alert({"job": "t"})  # 不抛即通过


def test_one_job_failure_does_not_block_others(monkeypatch):
    monkeypatch.setattr("deploy.scheduler._post_alert", lambda p: None)
    ran = []

    good = _job(_ok_cmd())
    good.name = "good"
    bad = _job(_fail_cmd(), retries=0)
    bad.name = "bad"

    orig = run_job

    def _tracking_run(job, sleep):
        ran.append(job.name)
        return orig(job, sleep=sleep)

    monkeypatch.setattr("deploy.scheduler.run_job", _tracking_run)
    clock = {"t": 0.0}
    loop([bad, good], now=lambda: clock["t"], sleep=lambda s: None, max_ticks=1)
    assert ran == ["bad", "good"]  # bad 失败后 good 照常执行


def test_interval_env_override_and_disable(monkeypatch):
    monkeypatch.setenv("CRON_IDLE_SESSIONS_INTERVAL", "0")  # 停用
    monkeypatch.setenv("CRON_KB_SCHEDULE_INTERVAL", "120")
    monkeypatch.delenv("CRON_QUALITY_VIEWS_INTERVAL", raising=False)
    jobs = {j.name: j for j in build_jobs()}
    assert "close_idle_sessions" not in jobs  # 0=停用
    assert jobs["kb_schedule"].interval == 120
    assert jobs["refresh_quality_views"].interval == 3600
    assert _env_interval("CRON_KB_SCHEDULE_INTERVAL", 1) == 120
    monkeypatch.setenv("CRON_KB_SCHEDULE_INTERVAL", "bad")
    assert _env_interval("CRON_KB_SCHEDULE_INTERVAL", 7) == 7  # 非法值回退


def test_loop_respects_due_time():
    """未到期不执行；到期后按间隔重排。"""
    executed = []
    job = _job(_ok_cmd(), interval=100.0)
    job.name = "due"
    clock = {"t": 0.0}

    def _sleep(seconds):
        clock["t"] += seconds

    import deploy.scheduler as sched

    def _track(j, sleep):
        executed.append(round(clock["t"]))
        return True

    sched_run = sched.run_job
    try:
        sched.run_job = _track
        loop([job], now=lambda: clock["t"], sleep=_sleep, max_ticks=3)
    finally:
        sched.run_job = sched_run
    # tick1 t=0 执行（next_due=100），tick2 睡到 100 执行，tick3 到 200
    assert executed == [0, 100, 200]


# ---------------- 编排文件静态一致性 ----------------


def test_prod_compose_structure():
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    services = compose["services"]
    for name in ("api", "cron", "migrate", "mcp", "postgres", "redis", "milvus"):
        assert name in services, f"缺服务 {name}"
    # cron 引用的调度器与脚本必须存在（防重命名漂移）
    assert (ROOT / "deploy/scheduler.py").exists()
    assert services["cron"]["command"] == ["python", "deploy/scheduler.py"]
    for script in ("close_idle_sessions.py", "kb_schedule.py", "refresh_quality_views.py"):
        assert (ROOT / "scripts" / script).exists()
    # api：生产硬门禁与多进程指标前提
    api_env = services["api"]["environment"]
    assert api_env["APP_ENV"] == "prod"
    assert "PROMETHEUS_MULTIPROC_DIR" in api_env
    # 就绪探针打 ready 端点
    assert "/api/health/ready" in " ".join(services["api"]["healthcheck"]["test"])
    # 不允许出现明文口令（强制 .env 插值）
    raw = (ROOT / "docker-compose.prod.yml").read_text()
    assert "POSTGRES_PASSWORD:?" in raw
    assert "postgres:postgres@" not in raw


def test_dockerfile_references_exist():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "uv sync --frozen" in dockerfile
    assert "USER appuser" in dockerfile  # 非 root 运行
    assert (ROOT / "uv.lock").exists()
    ignore = (ROOT / ".dockerignore").read_text()
    assert "models/" in ignore and ".env" in ignore
