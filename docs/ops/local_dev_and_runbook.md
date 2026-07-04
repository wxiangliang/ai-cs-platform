# 本地开发与运行说明

---

## 1. 启动本地依赖（PostgreSQL / Redis / Milvus）

仓库根目录已提供 `docker-compose.yml`（连接串与 `.env.example` 默认值对齐）：

```bash
docker compose up -d                 # PG + Redis（全阶段必需）
docker compose --profile kb up -d    # 加 Milvus standalone（Stage 06 知识库）
```

依赖清单（按阶段）：

```text
PostgreSQL 16+   全阶段必需
Redis 7+         全阶段必需
Milvus 2.5+      Stage 06 知识库检索必需（KB_ENABLED=true 且 KB_BACKEND=milvus）；
                 未部署时设 KB_ENABLED=false，聊天主链路不受影响，仅无 RAG 能力
```

Milvus 单机版启动（standalone，内嵌 etcd + 本地存储，无需 minio）：

```bash
# 1. 准备 etcd 配置
cat > embedEtcd.yaml <<'YAML'
listen-client-urls: http://0.0.0.0:2379
advertise-client-urls: http://0.0.0.0:2379
quota-backend-bytes: 4294967296
auto-compaction-mode: revision
auto-compaction-retention: '1000'
YAML

# 2. 启动容器
docker run -d --name milvus-standalone --security-opt seccomp:unconfined \
  -e ETCD_USE_EMBED=true -e ETCD_DATA_DIR=/var/lib/milvus/etcd \
  -e ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml -e COMMON_STORAGETYPE=local \
  -v $(pwd)/embedEtcd.yaml:/milvus/configs/embedEtcd.yaml \
  -v milvus_kb_data:/var/lib/milvus \
  -p 19530:19530 -p 9091:9091 milvusdb/milvus:v2.5.10 milvus run standalone

# 3. 探活
curl http://localhost:9091/healthz     # 返回 OK 即就绪
```

MCP 业务工具服务（TOOL_PROVIDER=mcp 时需要）：

```bash
uv run python scripts/run_mcp_server.py --port 8400   # 订单/物流查询 MCP 工具
# 服务未启动时平台自动回落进程内 mock（degraded 标记），不影响可用性
```

知识库常用操作：

```bash
# 从 PG（事实来源）全量重建 Milvus 索引（后端故障恢复 / 换 embedding 模型）
uv run python -m app.kb.reindex --tenant <tenant_id>
uv run python -m app.kb.reindex --tenant <tenant_id> --recompute-embedding
```

---

## 2. 启动服务

```bash
uv run uvicorn app.main:app --reload
```

---

## 3. 健康检查

```bash
curl http://localhost:8000/api/health
```

---

## 4. 数据库迁移

生成 migration：

```bash
uv run alembic revision --autogenerate -m "create chat core tables"
```

执行 migration：

```bash
uv run alembic upgrade head
```

---

## 5. 常见问题

```text
1. DATABASE_URL 必须使用 postgresql+asyncpg://
2. Redis 连接失败时检查 docker compose 是否启动。
3. Alembic autogenerate 找不到模型时，检查 app/db/base.py 是否导入模型。
4. 不要手工改数据库结构后忘记 migration。
5. RAG 全部拒答时先查 decision_log.retrieval_json 里的命中分数：
   EMBEDDING_PROVIDER=hash（开发模式）下相似度整体偏低，
   需在 .env 降低 FAQ_HIT_THRESHOLD / RAG_MIN_SCORE（建议 0.6 / 0.2~0.25）。
6. Milvus 挂掉不影响聊天主链路（rag_answer 节点内降级），
   /api/health/ready 的 kb_milvus 会显示 down；恢复后跑 reindex 兜底。
```

---

## 可观测与质量运维（Stage 09）

```bash
curl http://localhost:8000/metrics                     # Prometheus 指标（生产限内网访问）
uv run python scripts/refresh_quality_views.py         # 刷新 quality_daily（建议定时每小时）
uv run python scripts/export_review_set.py --tenant t1 # 回流待审样本导出（--mode faq 为 FAQ 沉淀）
uv run python scripts/replay_trace.py --trace <id>     # 按 X-Trace-Id 回放一轮决策
```

看板指标口径与 SQL：`docs/ops/quality_queries.md`。
会话生命周期（Stage 15）：`uv run python scripts/close_idle_sessions.py` 建议 cron 每小时
（空闲会话关闭 + CSAT 询问 + 超时工单关单归还）。
回流闭环：导出 → 人工改标 → `build_intent_dataset.py --extra <csv>` → 重训 → `pytest tests/eval` 门禁。


---

## 生产部署注意（Stage 13）

```text
1. APP_ENV=staging|prod 有配置硬门禁：AUTH_ENABLED 必须开启、DEBUG=false、
   EMBEDDING_PROVIDER 不得为 hash（除非 KB_ENABLED=false）、DATABASE_URL 不得用默认
   弱口令——缺项启动即报错并列出清单（fail-fast）。
2. 开发模式管理面（kb/product/handoff）也必须配置 KB_ADMIN_TOKEN（空 token 一律 403）。
3. 多 worker 部署：设置 PROMETHEUS_MULTIPROC_DIR=<可写目录> 启用指标跨进程聚合，
   否则 /metrics 只反映单个 worker。
4. SetFit 模型每 worker 各加载一份（transformer 常驻内存）：建议单 worker 容器水平扩容，
   或后续拆独立推理服务；首个请求触发懒加载会有冷启动尖峰，可预热。
5. MCP 降级策略默认 TOOL_MCP_FALLBACK=fail（查询失败如实告知用户，不用 mock 数据冒充）；
   仅开发联调可设 mock。
6. API Key 吊销：scripts/manage_api_keys.py disable 会经 Redis 广播即时失效所有进程缓存；
   Redis 不可达时回落 AUTH_CACHE_TTL（默认 300s）自然过期。
```
