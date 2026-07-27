# Stage 24：部署编排与定时任务（post-stage-23 规划 B1）

> 编号说明：post-stage-23 规划中 B1 的候选编号为 27，因按执行顺序先行开工，
> 实际落为 Stage 24。规划文档中的候选编号一律以实际实现为准。

## 1. 阶段目标

当前项目只有开发态依赖编排（docker-compose 起 PG/Redis/Milvus），应用本身
无镜像、无生产编排；三个运维脚本（会话生命周期/知识库定时生效/质量视图刷新）
依赖人肉或外部 cron，断跑无告警。本阶段交付**可复现的一键生产部署**（后端侧）：
应用镜像、生产 compose 编排、自带重试与告警的任务调度器、部署运维文档。

## 2. 本阶段要做什么

```text
1. Dockerfile（uv 多阶段：锁文件依赖层缓存、非 root 运行、.venv 直用）+ .dockerignore；
2. docker-compose.prod.yml 生产编排：
   - api：应用镜像，uvicorn 多 worker + PROMETHEUS_MULTIPROC_DIR（tmpfs）、
     就绪探针打 /api/health/ready（DB/Redis 探活）、restart 策略、env_file 注入；
   - cron：同一镜像跑 deploy/scheduler.py（见 3）；
   - mcp（profile mcp）：TOOL_PROVIDER=mcp 时启用的工具服务（:8400）；
   - postgres / redis / milvus(profile kb)：带 restart 策略与健康检查；
   - migrate 一次性任务：alembic upgrade head（compose run 方式，文档给命令）。
3. deploy/scheduler.py 任务调度器（纯标准库，单一容器承载三个 cron）：
   - 任务表：close_idle_sessions（默认 600s）/ kb_schedule（600s）/
     refresh_quality_views（3600s），间隔可用 CRON_*_INTERVAL 环境变量覆盖，0=停用；
   - 每次执行失败自动重试（默认 2 次，间隔递增）；最终失败 →
     ERROR 日志 + ALERT_WEBHOOK_URL（配置了才发，POST JSON，best-effort）；
   - 输出结构化日志（job/rc/耗时/重试次数），容器 stdout 即运行日志。
4. docs/ops/deployment.md 生产部署清单：构建/迁移/启动/扩容/探针/告警/
   备份要点/模型产物挂载（models/ 不进镜像，SetFit 无产物自动降级规则）。
```

## 3. 本阶段不做什么

```text
1. 不做 K8s manifests（compose 为第一形态；K8s 迁移时 api/cron/mcp 直接
   映射为 Deployment/CronJob/Service，文档给映射说明即可）；
2. 不做 Grafana 看板与告警规则（规划 B2，另立阶段）；
3. 不做压测与容量标定（规划 B3）；
4. 不动应用代码（部署纯外围；调度器不 import app 包，纯 stdlib 保持轻量）。
```

## 4. 技术要求

```text
1. 镜像非 root 运行；依赖层与代码层分离（改代码不重装依赖）；
2. 就绪探针必须打 /api/health/ready（含 DB/Redis 探活），存活探针 /api/health；
   容器内无 curl，healthcheck 用 python urllib；
3. 调度器任一任务失败不影响其他任务；告警发送失败只记日志（best-effort）；
4. 敏感配置一律 env_file 注入，compose 文件里不出现明文密钥（既有约束）；
5. 生产 compose 必须显式设置 APP_ENV=prod 生效 Stage 13 配置硬门禁。
```

## 5. 目录和文件要求

```text
Dockerfile / .dockerignore / docker-compose.prod.yml
deploy/
  __init__.py
  scheduler.py            # 定时任务调度器（重试 + 告警 + 结构化日志）
docs/ops/deployment.md    # 生产部署清单
tests/stage24/test_deployment.py
```

## 6. 测试与验收

```text
1. deploy/scheduler.py 单测：到期判定 / 重试次数 / 最终失败触发告警 /
   告警未配置不发 / 单任务失败不影响其他任务 / 间隔 0 停用；
2. 编排静态校验：docker compose -f docker-compose.prod.yml config 通过；
   compose 引用的脚本/文件存在性断言（防重命名漂移）；
3. 部署文档步骤可从零复现（评审口径；本环境不做全量镜像构建——
   模型依赖体积大，构建验证留给 CI/部署环境）；
4. 全量回归零失败（不含已知环境项）。
```

---

## 附录：实现记录（2026-07-27）

- `Dockerfile`：uv 多阶段（锁文件依赖层缓存 + 代码层分离）、非 root（uid 10001）、
  `.venv` 入 PATH；`.dockerignore` 裁掉 models/data/docs/tests/.env；
- `docker-compose.prod.yml`：api（APP_ENV=prod 触发硬门禁、
  PROMETHEUS_MULTIPROC_DIR tmpfs、ready 探针用 python urllib、models 只读挂载）/
  cron / migrate（profile tools 一次性）/ mcp（profile mcp）/
  postgres·redis·milvus（restart + healthcheck）；口令零明文——
  `${POSTGRES_PASSWORD:?}` 强制 .env 插值（env_file required:false，
  缺 .env 时由插值兜底拒启）；`docker compose config` 全 profile 校验通过；
- `deploy/scheduler.py`：纯标准库（不 import app 包）；三任务默认
  600/600/3600s，`CRON_*_INTERVAL` 覆盖、0=停用；失败重试 2 次线性退避、
  最终失败 POST `ALERT_WEBHOOK_URL`（best-effort）；单任务失败不影响其他；
  结构化日志走容器 stdout；
- `docs/ops/deployment.md`：从零部署步骤/必改配置表/扩容与探针口径/
  cron 与告警/备份要点/K8s 映射说明/已知限制；
- 测试：`tests/stage24/` 8 例（重试与告警/无 webhook 不发/单任务失败隔离/
  到期与重排/间隔覆盖与停用/compose 结构与零明文口令断言/Dockerfile 断言）。
  全量 340 passed。
- 遗留：镜像全量构建验证留给 CI/部署环境（torch 体积）；镜像瘦身
  （无模型推理镜像）按需；B2 告警规则覆盖「cron 容器挂掉」场景。
