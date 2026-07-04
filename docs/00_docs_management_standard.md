# 文档管理规范

本规范用于约束 `ai-cs-platform` 项目的所有需求文档、架构文档、数据库文档、API 文档和 AI/Codex 执行文档。

---

## 1. 总原则

```text
1. AGENTS.md 只做全局入口，不承载长需求。
2. 详细需求必须放在 docs/ 下。
3. 一个需求一个 md 文件。
4. 每个阶段单独一个目录。
5. 文件名必须体现需求名称。
6. Codex 每次只执行一个阶段文件，避免一次性生成过多代码。
7. 文档先行，代码后写。
8. 表结构通过 SQLAlchemy Model + Alembic migration 管理，不手工建表。
```

---

## 2. 目录分类

```text
docs/requirements/      需求文档
docs/architecture/      架构设计
docs/database/          数据库设计
docs/api/               API 设计
docs/prompts/           Prompt / Skill / Guardrails 规范
docs/testing/           测试策略
docs/ops/               部署、运行、排查
```

---

## 3. 阶段文档命名规范

```text
docs/requirements/stage-01-foundation/01_foundation_framework.md
docs/requirements/stage-02-chat-tables/01_chat_core_tables.md
docs/requirements/stage-03-chat-main-chain/01_chat_main_chain.md
```

格式：

```text
stage-编号-阶段英文名/编号_需求英文名.md
```

---

## 4. 每个阶段文档必须包含

```text
1. 阶段目标
2. 本阶段要做什么
3. 本阶段不做什么
4. 技术要求
5. 目录和文件要求
6. 具体实现要求
7. 代码质量要求
8. 验证方式
9. Codex 执行提示词
```

---

## 5. 文档更新规则

当需求变化时：

```text
1. 优先修改对应 docs 文件。
2. 不要直接让 Codex 根据聊天记录猜需求。
3. 需求变化较大时，新建一个补充 md。
4. 老文档不要随便删除，可以标记 deprecated。
5. 阶段执行完成后，可以在文档底部记录完成情况。
```

---

## 6. 给 Codex 的固定开头

```text
请先阅读根目录 AGENTS.md，
再阅读本次任务相关的 docs 文档。

严格按文档实现，不要超范围实现。
如发现文档冲突，以 AGENTS.md 的全局规则和当前阶段文档为准。
完成后说明新增文件、修改文件、启动方式、验证方式。
```
