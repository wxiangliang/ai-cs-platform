# 建表 SQL 脚本（DDL 参考）

> **本目录是生成物，勿手工修改。** 表结构的唯一事实来源是 `app/models/`（SQLAlchemy Model），
> 日常变更必须走 Alembic（`uv run alembic upgrade head`）——见 CLAUDE.md 强约束"不手工建表"。
> 本目录用途：DBA 审阅、新环境一次性初始化参考、与外部团队对齐表结构。

## 文件清单

| 文件 | 内容 |
|---|---|
| `00_all_tables.sql` | 全量建表（10 张表，按依赖顺序，含索引与中文注释） |
| `01-04_chat_*.sql` | 聊天核心：decision_log / dialog_state / message / session（Stage 02-03） |
| `05-06_chat_task / chat_tool_call.sql` | 任务生命周期与工具调用审计（Stage 05） |
| `07-09_faq_entry / kb_chunk / kb_document.sql` | 知识库三表（Stage 06，PG 为事实来源） |
| `10_product_item.sql` | 本地商品库（Stage 06-03） |

## 重新生成（模型变更后）

```bash
uv run python scripts/export_table_ddl.py
```

## 与设计文档的关系

字段语义、枚举取值域、索引与外键决策的解释见 `docs/database/chat_tables.md`；
本目录只提供可执行的 DDL 本体（已在临时库全量执行验证）。

注意：`alembic_version` 表不在此列（由 Alembic 自建）；Milvus collection
（kb_chunk_v1 / kb_faq_v1）非关系表，由应用启动时自动创建（见 stage-06 文档）。
