# Stage 06-03 需求：检索路由（RAG 接入时机）与商品库结合

> 前置阅读：`01_rag_faq_knowledge_base.md`、`docs/chat/intent_taxonomy.md`。
> 回答三个架构问题：① 聊天链路**什么时候**查 RAG；② 商品库**什么时候**查、与 RAG 怎么结合；
> ③ FAQ 与 RAG 的区别、是否需要独立存在。

---

## 1. 检索时机总原则

**按「意图 × 对话状态」路由检索，绝不每轮盲查。**

1. **任务流轮次不检索**：补槽中（COLLECTING 追问订单号）、确认门中（CONFIRMING）的轮次
   目标是推进任务，插入检索只会增加延迟、且检索结果无处安放；
2. **只有「本轮要产出信息型答案」的轮次才检索**：意图明确是信息诉求、且必要槽位已齐；
3. **结构化源优先于非结构化源**：同一个问题，商品库/工具（事实源）> FAQ（人工标准答案）>
   知识库 RAG（文档片段生成），越靠前越准、越便宜、越不会幻觉；
4. **检索失败/超时不打断主链路**：一律降级到下一级或模板话术（已有 rag_answer 节点韧性约定）。

## 2. 检索路由矩阵（核心交付）

| # | 触发点 | 进入条件 | 查询顺序 | 状态 |
|---|---|---|---|---|
| R1 | FAQ.GENERAL 意图 | 平台政策/规则类问答 | FAQ 精确层 → 知识库 RAG → 拒答话术 | ✅ 已实现（01 文档） |
| R2 | META.UNKNOWN 兜底 | 无进行中任务 | FAQ 精确层 → 知识库 RAG → 澄清话术 | ✅ 已实现 |
| R3 | 商品信息类意图（ASK_INFO / ASK_PRICE / ASK_STOCK） | 商品线索齐全（product_name/product_id 槽位就绪，状态机判 DONE） | **商品库结构化查询 → 商品知识 RAG 增强（仅描述类）→ 原模板** | ★ 本次实现 |
| R4 | 业务 Skill `rag_fallback: true` | 工具返回无结果 | 知识库 RAG | Stage 05 工具层接入 |
| R5 | 补槽 / 确认门轮次 | — | **不检索**（总原则 1） | 设计约束 |

### R3 商品路径的细化规则（回答「什么时候查商品」）

```text
PRODUCT.ASK_PRICE / ASK_STOCK / ASK_INFO 且商品槽位齐全：
1. ProductProvider.search(tenant, 商品线索) —— 结构化商品库
   ├─ 唯一命中 → 直接用商品库字段回答：
   │    价格/库存/规格 = 商品库字段（唯一事实源，护栏红线：禁止用 RAG 片段回答价格库存）
   │    ASK_INFO 再叠加一次商品知识 RAG（description 之外的说明书/长文），有命中附引用
   ├─ 多命中 → 列出候选让用户选（最多 3 个），不猜
   └─ 无命中 → ASK_INFO 走知识库 RAG 兜底；PRICE/STOCK 回「没找到该商品」模板（宁缺勿编）
2. PRODUCT.COMPARE / RECOMMEND：v1 维持模板引导（需要批量查询与筛选，Stage 05 工具层承接）
```

**为什么价格/库存禁止走 RAG**：文档快照会过期，价格错误是资损级事故；
guardrails 已有「价格必须来自工具/配置」红线，本设计把红线落到路由层——
RAG 只允许回答**描述性、时效不敏感**的商品内容（材质/用法/保养等）。

## 3. 商品库接入设计

- **协议化**：`ProductProvider`（async search/get，tenant 必传）——你们的真实商品系统
  未来以 HTTP Provider 接入（同 MinerU 模式），本次实现 `LocalProductProvider`
  （本地 `product_item` 表）作为默认实现与联调基线，可插拔替换。
- **`product_item` 表**（PG）：id / tenant_id / product_id(业务编码) / name / category /
  price(分) / stock / attrs_json(颜色尺寸等) / description / status / 时间戳；
  索引 (tenant_id, status)、(tenant_id, name)。
- 检索方式 v1：名称 ILIKE + jieba 关键词（同 kb 关键词路），不做商品向量（量小没必要，
  预留升级）。管理面 API：`POST /api/product/items`（临时 token 保护，同 kb）。
- **与知识库的分工**：商品库 = 结构化事实（价格/库存/规格）；
  kb_document(source_type=product) = 商品长文资料（说明书/评测）。同一商品两边都可有，
  路由层按 R3 规则取用，metadata 里用 product_id 关联过滤。

## 4. FAQ 与 RAG 的区别（回答「要不要单独接 FAQ」）

**要，且已经单独实现**（faq_entry 表 + 精确层，01 文档）。二者不可互相替代：

| 维度 | FAQ | RAG |
|---|---|---|
| 数据 | 人工维护的标准问答对 | 非结构化文档自动切分 |
| 匹配 | 问题向量高阈值精确命中 | 混合检索 top-k |
| 答案 | 标准答案原文，**零幻觉** | 片段生成/摘录，有幻觉风险（拒答+引用控制） |
| 适用 | 高频、答案固定（运费谁出/退货期限） | 长尾、答案分散在文档里 |
| 成本 | 一次向量比对，<100ms | 检索+生成，慢一个量级 |
| 运营 | hit_count 可观测，答案可即时修正 | 改答案要改文档重摄取 |

**协同闭环（本次补充的运营机制）**：FAQ 未命中而 RAG 回答了的问题会积累在
decision_log.retrieval_json；运营定期把**高频 RAG 问题沉淀为 FAQ**（答案人工审核后入
faq_entry），下次同类问题就走零幻觉快路径。这是 FAQ 必须独立存在的核心理由——
它是 RAG 质量的人工收敛层。

## 5. 实现范围（本次）

1. `app/product/`：ProductProvider 协议 + LocalProductProvider + product_item 表/迁移/repository + 管理 API；
2. 图路由升级：`skill_resolve` 后条件路由新增 product 分支（R3 条件）→ 新节点 `product_answer`；
3. `product_answer` 节点：按 R3 规则查商品库 → 组装事实回复（含多命中候选/无命中降级 RAG）；
   检索轨迹并入 `retrieval_json`（新增 `product_hits` 字段）；
4. R5 约束落地检查：现有路由已满足（COLLECTING/CONFIRMING 走 response_generate），补守护测试。

## 6. 本阶段不做

- 真实商品系统 HTTP Provider（协议就位，对接时只写一个类）；商品向量检索；
- COMPARE/RECOMMEND 的批量商品路径（Stage 05 工具层）；
- FAQ 自动沉淀（先人工运营，Stage 09 评估平台做半自动）。

## 7. 验证方式

1. 录入商品「小米空调 X1」（价格/库存）→「小米空调X1多少钱」→ 回复含商品库价格，
   decision_log.retrieval_json.product_hits 有记录；
2. 「小米空调X1有货吗」→ 库存事实回答；「小米空调X1怎么保养」（商品库无此字段）→
   RAG 增强路径（商品知识文档命中则附引用，未命中走「帮您核实」模板）；
3. 商品多命中 →「为您找到多款相关商品…」候选列表；无命中价格问题 → 不编造；
4. 补槽/确认门轮次不触发任何检索（决策日志无 retrieval_json）；
5. FAQ 与 RAG 回归（01 文档验证 1-5 不回退）。
