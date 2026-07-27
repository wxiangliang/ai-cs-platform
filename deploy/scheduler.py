"""定时任务调度器（Stage 24）：单容器承载三个运维 cron。

设计约束：
- **纯标准库**，不 import app 包——调度器挂了不该和应用逻辑有任何纠缠，
  升级应用依赖也不影响它；
- 每个任务独立：失败自动重试（间隔递增），最终失败发告警（配置了
  ALERT_WEBHOOK_URL 才发，best-effort，发送失败只记日志）；
  单任务失败绝不影响其他任务；
- 结构化日志到 stdout（容器日志即运行日志）；
- 间隔用环境变量覆盖，0 = 停用该任务。

生产迁 K8s 时本文件直接废弃，任务表映射为三个 CronJob（见 deployment.md）。
"""

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s scheduler %(message)s"
)
logger = logging.getLogger("scheduler")

# 最终失败告警的输出截断长度
_ALERT_TAIL_CHARS = 800


@dataclass
class Job:
    """一个定时任务：间隔到期后以子进程执行 cmd。"""

    name: str
    cmd: list[str]
    interval: float  # 秒；<=0 表示停用
    retries: int = 2  # 失败后的额外重试次数
    retry_delay: float = 30.0  # 首次重试等待（之后线性递增）
    timeout: float = 600.0  # 单次执行超时
    next_due: float = field(default=0.0, compare=False)  # 0=启动即执行一次


def _env_interval(name: str, default: float) -> float:
    """从环境变量读任务间隔（非法值回退默认）。"""
    raw = os.environ.get(name, "")
    try:
        return float(raw) if raw else default
    except ValueError:
        logger.warning("invalid interval %s=%r, use default %s", name, raw, default)
        return default


def build_jobs() -> list[Job]:
    """任务表（间隔可被 CRON_*_INTERVAL 覆盖，0=停用）。"""
    python = sys.executable
    jobs = [
        Job(
            name="close_idle_sessions",
            cmd=[python, "scripts/close_idle_sessions.py"],
            interval=_env_interval("CRON_IDLE_SESSIONS_INTERVAL", 600),
        ),
        Job(
            name="kb_schedule",
            cmd=[python, "scripts/kb_schedule.py"],
            interval=_env_interval("CRON_KB_SCHEDULE_INTERVAL", 600),
        ),
        Job(
            name="refresh_quality_views",
            cmd=[python, "scripts/refresh_quality_views.py"],
            interval=_env_interval("CRON_QUALITY_VIEWS_INTERVAL", 3600),
        ),
    ]
    enabled = [job for job in jobs if job.interval > 0]
    for job in jobs:
        if job.interval <= 0:
            logger.info("job disabled: %s", job.name)
    return enabled


def _post_alert(payload: dict) -> None:
    """最终失败告警（best-effort）：未配置 webhook 则跳过，发送失败只记日志。"""
    url = os.environ.get("ALERT_WEBHOOK_URL", "")
    if not url:
        return
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:  # noqa: BLE001 - 告警失败不能拖垮调度器
        logger.exception("alert webhook failed (job=%s)", payload.get("job"))


def _run_once(job: Job) -> tuple[int, str]:
    """执行一次任务，返回 (returncode, 合并输出尾部)。"""
    try:
        proc = subprocess.run(
            job.cmd, capture_output=True, text=True, timeout=job.timeout
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, output[-_ALERT_TAIL_CHARS:]
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {job.timeout}s"
    except Exception as exc:  # noqa: BLE001 - 启动子进程失败也按任务失败处理
        return -1, f"spawn failed: {exc}"


def run_job(job: Job, *, sleep=time.sleep) -> bool:
    """带重试执行任务；最终失败发告警。返回是否成功。

    sleep 参数化便于测试（不真等重试间隔）。
    """
    start = time.monotonic()
    for attempt in range(job.retries + 1):
        rc, tail = _run_once(job)
        elapsed = round(time.monotonic() - start, 1)
        if rc == 0:
            logger.info(
                "job ok: %s (attempt=%d elapsed=%ss)", job.name, attempt + 1, elapsed
            )
            return True
        logger.warning(
            "job failed: %s rc=%s attempt=%d/%d", job.name, rc, attempt + 1, job.retries + 1
        )
        if attempt < job.retries:
            sleep(job.retry_delay * (attempt + 1))  # 线性递增退避
    logger.error("job failed permanently: %s", job.name)
    _post_alert({"job": job.name, "rc": rc, "output_tail": tail, "elapsed_s": elapsed})
    return False


def loop(jobs: list[Job], *, now=time.monotonic, sleep=time.sleep, max_ticks: int | None = None) -> None:
    """主循环：到期任务顺序执行（单容器场景串行足够，互不抢 DB）。

    max_ticks 仅供测试限制循环次数。
    """
    if not jobs:
        logger.warning("no jobs enabled, scheduler exits")
        return
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        current = now()
        for job in jobs:
            if current >= job.next_due:
                run_job(job, sleep=sleep)
                job.next_due = now() + job.interval
        upcoming = min(job.next_due for job in jobs)
        sleep(max(1.0, upcoming - now()))


if __name__ == "__main__":
    logger.info("scheduler starting (pid=%s)", os.getpid())
    loop(build_jobs())
