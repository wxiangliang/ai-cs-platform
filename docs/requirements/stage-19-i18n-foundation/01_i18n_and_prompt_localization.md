# Stage 19 需求：多语言地基（i18n 收口 + 提示词国际化）

> 前置：无（纯重构 + 提示词加指令，不依赖真实模型/训练数据）。
> 本阶段是「多语言」的第一步——**只打地基，行为不变（输出仍是中文）**，
> 让将来支持新语言从「改遍全代码」变成「加一个语言包」。
> 完整多语言的「听懂非中文输入」（意图分类多语言训练集、护栏词表/槽位正则多语言）
> 卡训练数据，不在本阶段，属后续。
> 来源：用户提出的架构问题——面向用户文案与提示词现散在 ~18 个文件硬编码，
> 将来加语言会很痛。

---

## 1. 阶段目标

把「输出侧」的多语言地基铺好：面向用户的确定性文案统一收口、LLM 提示词国际化、
`locale` 贯穿全链路。做完后现有系统行为**逐字不变**（默认语言 zh），
但新增一门语言 = 补一个语言包 + 解决输入理解，地基不返工。

## 2. 核心设计（两类文本，两种办法）

**关键区分**：面向用户的文本分两类，多语言办法完全不同——

1. **LLM 生成的文本**（润色/RAG 生成/意图二判/确认门解析/记忆摘要）——**不需要翻译提示词**。
   LLM 本身多语言：system prompt 指令保持一套，加一条「用用户的语言（{locale}）回复」，
   把 locale 传进去即可。用户发英文/日文，模型自动用该语言答。事实保护（数字/订单号原样）语言无关，已有。
2. **确定性文案**（模板回复、护栏话术、CSAT 询问、转人工/续办/排队等系统话术）——**必须 i18n**。
   这些不经 LLM，是写死字符串，按 locale 查表。中文作为默认/源语言。

## 3. 本阶段要做什么

### 3.1 i18n 资源层

- `app/core/i18n.py`：`t(key, locale, **params) -> str`——按 key + locale 查文案，
  参数插值沿用现有占位符风格（`{order_id}` 等，与 responder 的 `format_map` 一致）；
- 语言包：`app/locales/zh.py`（或 yaml/json，中文，**唯一实际语言包=现有全部文案**）+
  `app/locales/en.py`（**骨架示范**，仅补几个 key 证明机制，其余留空）；
- **缺失回退**：目标 locale 缺某 key → 回退 `LOCALE_DEFAULT`（zh），绝不崩/不空串；
- key 命名规范：`domain.action`（如 `handoff.waiting`/`guardrail.injection`/`csat.ask`/`queue.position`）。

### 3.2 确定性文案收口（散落 18 文件 → catalog）

- 面向用户的硬编码中文迁到 catalog，代码改 `t(key, locale, ...)`：
  - `guardrail_check`（`_BLOCK_REPLIES` 三类话术）、`response_generate`（静默/CSAT 致谢/弱确认重确认）、
  - `handoff_service`（等待/CSAT 询问/工单号话术）、`save_turn`（续办提示/排队/连续兜底转人工）、
  - `csat.py`、`close_idle_sessions.py`（CSAT 询问）、各回复节点的降级话术；
- **skill 模板**（`skill.templates`：collect/confirm/confirmed/answer，来自 skills_design md）：
  md 的 templates/ask_prompt 等扩为**按语言**（`templates.zh` / `templates.en`），
  loader 读取时按 locale 选；v1 只填 zh，其它语言留空走回退。保持「skill 文案文件化」的既有优点。

### 3.3 LLM 提示词国际化

- `app/chat/llm/prompts.py` / `answerer.py` / `confirmation/parser.py` / `memory/local_provider.py`：
  system prompt 加「用用户的语言回复」指令（指令本身可保持中文，模型能懂），
  并把 `locale` 注入（供模型知道目标语言）；
- 已有的 `wrap_user_input()`（Stage 14 防注入）不受影响。

### 3.4 locale 贯穿

- 请求：`ChatMessageRequest` 加 `locale`（默认 `zh`，BCP-47 简码 zh/en/ja…）；
- 会话记忆：首轮 locale 记入会话（`chat_session.metadata_json` 或 dialog context），后续轮沿用，除非重新指定；
- `GraphState` 加 `locale`，所有 `render_reply`/`t()`/prompt 组装从 state 取；
- `LOCALE_DEFAULT` 配置项（默认 zh）；渠道（channel）已知语言时可由接入层带入。

## 4. 本阶段不做什么

- **意图分类多语言**（SetFit 需多语言训练集）——非中文输入的理解属后续；
- **护栏词表 / 槽位正则多语言**（辱骂/注入词表、订单号/手机号格式按语言配）；
- **实际翻译**中文文案到其它语言（本阶段只搭架子 + zh 源语言包 + en 骨架示范，
  真翻译交付时机翻+人工校，不占本阶段）；
- **自动语言检测**（v1 靠请求传入/默认；预留 `detect_locale` 扩展位，接检测/翻译 API 后启用）。

## 5. 目录和文件要求

```text
app/core/i18n.py                         # t(key, locale, **params) + 回退
app/locales/zh.py + en.py                # 语言包（zh 全量=现有文案；en 骨架示范）
app/schemas/chat.py                      # ChatMessageRequest 加 locale
app/chat/graph/state.py                  # GraphState 加 locale
app/chat/skills/responder.py + loader.py # 模板按语言选取
app/chat/skills/types.py                 # Skill.templates 扩语言维度
app/chat/graph/nodes/*.py                # 硬编码文案改 t()
app/services/handoff_service.py / csat.py / scripts/close_idle_sessions.py
app/chat/llm/prompts.py + answerer.py + parser.py + memory  # prompt 加语言指示
app/core/config.py                       # LOCALE_DEFAULT
docs/chat/skills_design/skills/*.md      # templates 扩语言维度（v1 只填 zh）
tests/stage19/
```

## 6. 验证方式

1. **零回归（最重要）**：默认 `locale=zh` 时,全部面向用户输出与现在**逐字一致**——
   现有 e2e/单测的中文回复断言全绿即守护达标（i18n 收口是纯重构，不许改变现有中文输出）。
2. `locale=en` + en 骨架包：被补的几个 key 走英文，未补的回退中文，不崩。
3. 缺失回退：请求 `locale=ja`（无日文包）→ 全部回退中文，无空串/异常。
4. LLM prompt：system prompt 含「按用户语言回复」+ locale 注入；`OPENAI_API_KEY` 为空时模板路径照常（i18n 不依赖 LLM）。
5. locale 贯穿：请求带 locale → 会话记住 → 后续轮模板与 prompt 都用它。
6. 全链路 ruff/mypy/pytest 绿。

## 7. 收益与后续

- **本阶段后**：新增一门语言 = 加一个 `app/locales/xx.py` 语言包 + skill md 的 `templates.xx`，
  代码零改；LLM 生成路径无需任何语言包（模型按用户语言答）。
- **后续（真要上某门语言时）**：补该语言的意图分类样本（或用翻译 API 转中文再分类的过渡方案）、
  护栏词表、槽位正则格式——这些才是需要数据/逐语言配的部分。

---

## 附录：实现记录（2026-07-04）

### A. 已实现清单

| 项 | 实现 | 说明 |
|---|---|---|
| i18n 资源层 | `app/core/i18n.py`（`t(key, locale, **params)` / `skill_template` / `resolve_locale`）+ `app/locales/`（`zh.py` 源语言全量、`en.py` 骨架示范、`__init__.py` 汇总） | 缺 key 回退默认语言→再缺回退 key 本身并告警；`_SafeDict` 缺参数空串不崩；locale 不在 SUPPORTED_LOCALES 回退默认 |
| 确定性文案收口 | responder（模板兜底）、guardrail_check（注入/违禁/灌注/连击建单话术）、response_generate（静默/CSAT 致谢/弱确认/放弃）、save_turn（续办/排队/工单号/连续兜底）、action_execute（回执/失败）、product_answer（多命中/无命中）、handoff_service+close_idle（CSAT 询问）→ 全部 `t(key, locale)` | zh 值逐字对齐迁移前硬编码，**零回归**（213 tests 含大量中文断言全绿） |
| skill 模板语言覆盖 | registry 中文=zh 源不动；`render_reply(..., locale)` 非默认语言查 `skill.<id>.<key>` 覆盖，缺则回退 registry 中文 | 加语言=补语言包，registry 零改动 |
| LLM 提示词国际化 | 回复润色（prompts.py）、RAG 生成（answerer.py）system prompt 加「回复语言与用户消息保持一致」 | 不翻译提示词——模型按用户语言回复；二判/槽位/确认/记忆输出非面向用户，不加 |
| locale 贯穿 | 请求 `ChatMessageRequest.locale` → chat_service 注入 initial_state（locale 兜底 + locale_req 透传）→ load_session_state 决策（请求带的优先＞会话记忆＞默认）+ 记入会话 metadata → GraphState.locale → 各节点/render_reply/t 取用 | 新建会话带 locale 一并写入 metadata；后续轮不带则沿用记住的语言 |
| 配置 | `LOCALE_DEFAULT=zh` / `SUPPORTED_LOCALES=zh,en` | — |
| 测试 | `tests/stage19/test_i18n.py`（13 例） | t 查表/回退/参数、resolve_locale、skill 覆盖、render_reply 零回归+覆盖、prompt 语言指示、locale 决策+会话记忆、schema 字段 |

### B. 验证记录

- **零回归**：全量 213 tests 通过（新增 stage19 13 例），既有中文回复断言逐字通过——i18n 收口未改变默认语言输出。
- **e2e**：`locale=en` 发注入 → 英文护栏话术（`Sorry, I can't process...`）；`locale=zh` 同注入 → 原中文（逐字）；首轮 `locale=en` 记入会话，次轮不带 locale → 沿用 en（英文护栏话术）。
- ruff / mypy 干净。

### C. 收益与遗留

- 加一门语言 = 加 `app/locales/xx.py` 语言包 + skill md 的 `templates.xx`，代码零改；LLM 生成路径无需语言包。
- 遗留（真要上某门语言时）：意图分类多语言样本（或翻译成中文再分类）、护栏词表/槽位正则多语言、skill.name 多语言（续办提示里的技能名当前仍中文）、自动语言检测（v1 靠请求传入/默认）。
