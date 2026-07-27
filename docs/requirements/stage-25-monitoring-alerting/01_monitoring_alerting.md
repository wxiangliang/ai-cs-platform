# Stage 25：监控告警落地（post-stage-23 规划 B2）

## 1. 阶段目标

Prometheus 指标已有 17 个（单一注册点 `app/core/metrics.py` + `/metrics`），
但缺三样：可视化看板、告警规则、以及 post-stage-19 指出的**高基数 label 违规**
（`llm_tokens_total{tenant}` / `llm_budget_exceeded_total{tenant}` 与
「多租户不进 label」的模块原则冲突）。本阶段补齐：Grafana 看板 provisioning、
Prometheus 告警规则、指标基数整改、cron 容器存活监控（Stage 24 遗留）。

## 2. 本阶段要做什么

```text
1. 指标基数整改：llm_tokens_total 去掉 tenant label（保留 purpose）、
   llm_budget_exceeded_total 去掉 tenant label——Prometheus 只保聚合；
   租户级 token 明细走既有 Redis 日计数（budget 模块 llm_budget:{tenant}:{day}），
   查询口径写进运维文档；
2. deploy/monitoring/：prometheus.yml（抓 api /metrics）+ alerts.yml
   （告警规则：API down / 轮次 P95 / LLM 失败率 / 预算熔断激增 /
   护栏拦截激增 / RAG 拒答率 / 限流激增）+ Grafana provisioning
   （数据源 + 看板自动装载）+ 看板 JSON（轮次延迟/意图分布/RAG 结局/
   LLM 调用与 token/kb 分段耗时/护栏与纠偏/诊断 agent）+
   alertmanager.yml.example（对接 webhook 的样例，部署侧按需启用）；
3. docker-compose.prod.yml 增 monitoring profile：prometheus + grafana
   （provisioning 挂载，开箱即有看板）；
4. cron 容器存活（Stage 24 遗留）：scheduler 每轮 tick 写心跳文件，
   compose healthcheck 检查心跳时效，配合 restart 策略自愈；
5. docs/ops/monitoring.md：看板说明/告警规则清单与阈值依据/演练步骤/
   租户明细查询口径。
```

## 3. 本阶段不做什么

```text
1. 不做告警通知渠道对接（Alertmanager 起停与 webhook 地址是部署侧配置，
   给 example 文件与文档，不进默认编排）；
2. 不做长期存储/联邦（单 Prometheus 本地 TSDB 起步，保留期 15d 默认）；
3. 不做业务大盘（质量看板走 quality_daily SQL/BI，Stage 09 口径不变——
   Grafana 只管服务与链路健康）；
4. 阈值先给保守默认并标注「待 B3 压测标定」，不假装有依据。
```

## 4. 技术要求

```text
1. 指标改名/去 label 属破坏性变更：llm_tokens_total 的 tenant 维度删除前
   确认无消费方（当前无 Grafana/告警引用，decision_log/Redis 已有租户明细）；
2. 看板与告警只引用 metrics.py 实际存在的指标名（测试静态断言防漂移）；
3. Grafana 匿名只读关闭、admin 密码走 .env（GRAFANA_ADMIN_PASSWORD 强制插值，
   与 POSTGRES_PASSWORD 同纪律）；
4. 心跳文件路径可配（容器内 /tmp），healthcheck 时效 = 2 × 最短任务间隔。
```

## 5. 目录和文件要求

```text
deploy/monitoring/
  prometheus.yml / alerts.yml / alertmanager.yml.example
  grafana/provisioning/datasources/prometheus.yml
  grafana/provisioning/dashboards/default.yml
  grafana/dashboards/ai-cs-platform.json
deploy/scheduler.py          # 心跳文件
docker-compose.prod.yml      # monitoring profile + cron healthcheck
app/core/metrics.py          # 基数整改
docs/ops/monitoring.md
tests/stage25/test_monitoring.py
```

## 6. 测试与验收

```text
1. 基数整改：llm_tokens_total 仅 purpose label、llm_budget_exceeded_total
   无 label；全量回归零失败（调用方签名同步）；
2. 配置有效性：prometheus.yml/alerts.yml YAML 可解析；告警规则引用的
   指标名全部存在于 metrics.py（静态交叉断言）；看板 JSON 可解析且
   引用指标同样校验；compose monitoring profile config 校验通过；
3. 心跳：scheduler 每 tick 写心跳文件（单测断言）；compose cron healthcheck
   引用同一路径；
4. 演练步骤在 monitoring.md 中可执行（评审口径）。
```

---

## 附录：实现记录（2026-07-27）

- **基数整改**：`llm_tokens_total` 只留 purpose、`llm_budget_exceeded_total`
  去 label；调用方（budget/factory）同步；租户明细口径写入 monitoring.md
  第 4 节（Redis 日计数 + quality_daily SQL）；测试断言全表 label 永不出现
  tenant（防回潮）；
- **deploy/monitoring/**：prometheus.yml（抓 api /metrics + rule_files）、
  alerts.yml 8 条规则（ApiDown/P95/LLM 失败率/预算熔断/护栏激增/RAG 拒答率/
  限流/转人工激增，阈值标注「待 B3 标定」）、Grafana provisioning
  （数据源 uid=prometheus + 看板自动装载）、看板 JSON 10 面板、
  alertmanager.yml.example；
- **compose monitoring profile**：prometheus + grafana（密码
  `${GRAFANA_ADMIN_PASSWORD:?}` 强制插值，匿名访问关闭）；全 profile
  `docker compose config` 校验通过；
- **cron 存活收口**（Stage 24 遗留）：调度器每 tick 写心跳文件
  （SCHEDULER_HEARTBEAT_FILE），compose healthcheck 检查 20 分钟时效 +
  restart 自愈；
- 测试：`tests/stage25/` 7 例（label 断言/防回潮扫描/告警与看板引用指标名
  交叉校验/供给配置/compose 结构/心跳写入）。全量 347 passed。
- 遗留：告警阈值 B3 压测标定；Alertmanager 通知渠道部署侧启用；
  告警演练（monitoring.md 第 3 节）需在部署环境执行一次。
