# 生产部署清单（Stage 24，后端侧）

> 编排文件：`docker-compose.prod.yml`（应用镜像 `Dockerfile`、调度器 `deploy/scheduler.py`）。
> 本文是从零到可服务的完整步骤；开发环境仍用 `docker-compose.yml`（只起依赖）。

## 1. 前置准备

```bash
cp .env.example .env
```

`.env` 生产必改项（`APP_ENV=prod` 会触发 Stage 13 配置硬门禁，缺项**拒绝启动**）：

| 项 | 说明 |
|---|---|
| `POSTGRES_PASSWORD` | 强口令；compose 用它拼 DATABASE_URL（弱口令被硬门禁拦截） |
| `AUTH_ENABLED=true` | 生产必须开启；API Key 用 `scripts/manage_api_keys.py` 签发 |
| `DEBUG=false` | 硬门禁项 |
| `EMBEDDING_PROVIDER` | 生产禁 hash（硬门禁项）；openai + `OPENAI_API_KEY` |
| `KB_ADMIN_TOKEN` | 管理面 token（或走 admin scope API Key） |
| `ALERT_WEBHOOK_URL` | cron 最终失败告警（企微/钉钉/Slack webhook，留空不发） |

模型产物（可选）：`models/intent_setfit_v1/` 放到宿主机同名目录（compose 只读挂载）；
缺失时意图分类自动降级规则层，服务照常启动。

## 2. 构建与启动

```bash
docker compose -f docker-compose.prod.yml build                # 构建应用镜像
docker compose -f docker-compose.prod.yml up -d postgres redis # 先起依赖
docker compose -f docker-compose.prod.yml run --rm migrate     # Alembic 迁移（幂等）
docker compose -f docker-compose.prod.yml up -d                # api + cron
# 知识库 / MCP 工具服务按需：
docker compose -f docker-compose.prod.yml --profile kb up -d   # Milvus，随后重建索引：
docker compose -f docker-compose.prod.yml run --rm --no-deps api python -m app.kb.reindex --tenant <id>
docker compose -f docker-compose.prod.yml --profile mcp up -d  # TOOL_PROVIDER=mcp 时
```

验证：

```bash
curl -sf http://localhost:8000/api/health          # 存活
curl -sf http://localhost:8000/api/health/ready    # 就绪（DB/Redis 探活）
curl -sf http://localhost:8000/metrics | head      # Prometheus 指标
docker compose -f docker-compose.prod.yml logs cron --tail 20  # 调度器任务日志
```

## 3. 扩容与探针

- **api 多副本**：`docker compose -f docker-compose.prod.yml up -d --scale api=3`
  （去掉 `ports` 固定映射、前置网关/负载均衡；WS 连接数与心跳按规划走网关层）。
  单容器内已是 uvicorn `--workers 2` + `PROMETHEUS_MULTIPROC_DIR` 聚合；
- **探针口径**：存活 `/api/health`（进程活着）；就绪 `/api/health/ready`
  （含 DB/Redis 探活，依赖挂了摘流量）。compose healthcheck 已按就绪配置；
- **cron 单副本**即可（任务幂等，但无需并行）；多副本部署时保持 cron=1。

## 4. 定时任务与告警

`deploy/scheduler.py`（cron 容器）承载三个任务，间隔环境变量可调（秒，0=停用）：

| 任务 | 默认间隔 | 覆盖变量 |
|---|---|---|
| close_idle_sessions（会话生命周期 + CSAT 询问） | 600 | `CRON_IDLE_SESSIONS_INTERVAL` |
| kb_schedule（知识库定时生效/失效） | 600 | `CRON_KB_SCHEDULE_INTERVAL` |
| refresh_quality_views（quality_daily 刷新） | 3600 | `CRON_QUALITY_VIEWS_INTERVAL` |

失败自动重试 2 次（线性退避）；最终失败 POST `ALERT_WEBHOOK_URL`
（JSON：job/rc/output_tail），未配置则只记 ERROR 日志。
**断跑监控**：告警只覆盖"执行失败"；"容器挂了"由 `restart: unless-stopped` +
容器监控（B2 阶段的告警规则）兜底。

## 5. 备份要点

- PostgreSQL：`pg_data` 卷 + 定期 `pg_dump`（唯一事实来源，最高优先）；
- Milvus：`milvus_data` 卷；可随时 `python -m app.kb.reindex` 从 PG 重建，
  备份优先级低于 PG；
- Redis：AOF 已开启（缓存/限流/幂等数据，可容忍丢失重建）。

## 6. 迁移到 K8s（映射说明，本阶段不实现）

| compose 服务 | K8s 资源 |
|---|---|
| api | Deployment（多副本）+ Service + HPA；探针沿用 health/ready |
| cron | 三个 CronJob（`deploy/scheduler.py` 废弃，任务表直接映射） |
| mcp | Deployment + Service |
| postgres/redis/milvus | 托管服务或 StatefulSet（建议托管） |

## 7. 已知限制

- 镜像构建需要网络（基础镜像 + uv 依赖）；本仓库 CI/部署环境执行，
  提交时只做 `docker compose config` 静态校验与调度器单测；
- torch/setfit 使镜像偏大（GB 级）；如需瘦身可拆「无模型推理」镜像
  （SetFit 降级规则层），或后续把分类器独立成服务。
