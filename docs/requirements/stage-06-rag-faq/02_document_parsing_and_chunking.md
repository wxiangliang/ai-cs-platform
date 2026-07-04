# Stage 06-02 需求：文档解析管道与结构感知切分

> 前置阅读：`01_rag_faq_knowledge_base.md`（知识库基座，已实现）。
> 设计参考：RAGFlow 的模板化切分与版面还原思路（DeepDoc）、MinerU content_list 结构。
> 目标：把知识库的输入从「纯文本」升级为「常用办公格式」，
> 并让切分**保住文档结构语义**——这是 RAG 答案质量的第一决定因素。

---

## 1. 目标与格式范围

| 格式 | 解析路径（优先级从高到低） | 说明 |
|---|---|---|
| **PDF** | MinerU(HTTP) → Docling(本地) → pypdf(纯文本兜底) | 图文混排/扫描件必须走 MinerU 或 Docling（带版面分析与 OCR）；pypdf 兜底只保底不保质 |
| **Word (.docx)** | Docling → python-docx(内置) | 保留标题层级与表格 |
| **Excel (.xlsx)** | 内置(openpyxl) | 每个 sheet 转 Markdown 表格块；行组切分重复表头 |
| **Markdown (.md)** | 内置结构解析 | 标题/段落/表格直接成块 |
| **纯文本 (.txt)** | 内置 | 空行分段 |

**解析器链与降级**：每种格式配置一条解析器链，前一级不可用（未配置/未安装/调用失败）
自动降级下一级；全部失败则该文档标记解析失败并告警，**不入库半成品**。
解析器实现 `DocumentParser` 协议，新格式/新引擎只加一个 parser 文件。

## 2. 统一中间表示（Block IR）

所有解析器的输出统一为**块序列**，切分器只面对 IR、不感知来源格式：

```python
@dataclass
class Block:
    type: str          # heading / paragraph / table / image / code
    text: str          # 文本内容；table 为 Markdown 表格；image 为可用文本（caption/OCR）
    level: int = 0     # heading 层级（1-6），其余为 0
    page: int | None   # 页码（PDF）
    meta: dict         # img_path、表格行列数等
```

### 2.1 MinerU 接入（HTTP）

- 配置 `MINERU_API_URL`（为空则跳过该级）；调用 `POST {url}/file_parse`（multipart 上传，
  请求返回 content_list），超时 `MINERU_TIMEOUT`（默认 120s，解析大 PDF 慢）。
- content_list → Block 映射：`text_level>=1 → heading`；`type=text → paragraph`；
  `type=table → table`（table_body HTML 转 Markdown）；`type=image → image`
  （text=图片 caption + footnote，img_path 存 meta）；`type=equation → paragraph`。
- 兼容处理：若响应只有 `md_content`，走 Markdown 结构解析兜底。

### 2.2 Docling 接入（本地库）

- `DocumentConverter.convert()` → `export_to_markdown()` → 内置 Markdown 结构解析成 Block。
  v1 用 Markdown 中转（实现简单、表格/标题保留完好）；后续需要页码/坐标时再改为遍历
  DoclingDocument 原生节点。
- Docling 首次运行会下载版面模型，属可选依赖：导入失败仅禁用该级并告警，不影响启动。

## 3. 结构感知切分策略（核心设计）

> 原则：**切分边界跟着文档结构走，语义上下文显式注入每个 chunk**。
> 旧版按字符数盲切的 `split_chunks` 仅保留给纯文本兜底。

### 3.1 标题路径注入（防语义悬空）

按 heading 层级构建章节树，每个 chunk 的文本前注入其标题路径：

```text
[退换货政策 > 第二条 退货运费]
因商品质量问题退货的，运费由商家承担；无理由退货的，退货运费由买家承担。
```

这样「运费由买家承担」脱离上下文检索到时，embedding 与生成都知道它说的是退货运费
（RAGFlow 的 title-chain 同思路）。标题路径同时存入 chunk 的 `metadata_json.heading_path`。

### 3.2 段落聚合

- 同一章节内的段落贪心合并到 `KB_CHUNK_SIZE`（默认 500 字符），**永不跨章节合并**；
- 超长段落按句子边界二次切分，相邻块保留 `KB_CHUNK_OVERLAP` 重叠；
- 长度 < 30 字符的孤立小段（如列表项）并入相邻块，避免碎片 chunk 污染检索。

### 3.3 表格（语义最容易掉的地方）

- **整表优先**：表格转 Markdown 后作为独立 chunk，不与正文混切；
- **表格上下文注入**：chunk 文本 = 标题路径 + 表格前的引导句（表格前最近一个段落，截断 100 字）+ 表格本体；
- **大表按行组切分**：超过 chunk 上限时按行组拆分，**每个分片都重复表头行**（否则后续分片全是裸数据）；
- Excel 的每个 sheet 视为一个「章节 + 表格」，sheet 名作为标题路径。

### 3.4 图文混排 PDF 的图片处理

v1 策略（不做多模态 embedding，先保证图片语义不丢文本化线索）：

- MinerU 返回的 image 块：用 `caption + footnote` 作为图片的文本代理，
  与**图片前后各一个段落**合成一个 chunk（图片讲什么通常由邻近正文说明）；
- 无 caption 且无邻近正文的裸图 → 丢弃该块（无可检索文本），img_path 记入文档 metadata 供人工核查；
- 扫描版 PDF：MinerU OCR 已转文本，按普通段落处理；
- **预留**：Block IR 的 image 类型与 img_path 字段已就位，Stage 09+ 可平滑升级
  多模态 embedding（图片向量入独立 collection）而不动切分层。

### 3.5 代码块 / 公式

- Markdown 代码块整块保留不切；公式（MinerU equation）按普通段落归入所在章节。

## 4. 摄取流程升级

```text
POST /api/kb/documents/upload（multipart：file + tenant_id + title? + source_type）
  → 格式路由选解析器链 → Block IR → 结构感知切分 → embedding → 写 PG → 写 Milvus
原有 POST /api/kb/documents（纯文本 body）保留：md 内容走结构切分，其余走纯文本切分
```

- `kb_document` 新增列：`file_name`、`parser`（实际使用的解析器，审计用）；
  原文 raw_content 存解析后的 Markdown 全文（重建分块的依据，不存二进制）。
- chunk 的 `metadata_json` 记录：`heading_path`、`block_type`、`page`。

## 5. 配置

```text
MINERU_API_URL=            # 空 = 跳过 MinerU 级
MINERU_TIMEOUT=120
DOCLING_ENABLED=true       # false 或未安装 = 跳过 Docling 级
KB_CHUNK_SIZE=500 / KB_CHUNK_OVERLAP=50（沿用）
KB_MIN_CHUNK_CHARS=30      # 碎片合并阈值
```

## 6. 本阶段不做

- 图片多模态 embedding（IR 已预留）；PDF 双栏复杂版面自研（交给 MinerU/Docling）；
- 文档级权限（随 Stage 08 鉴权做）；ppt/邮件/网页格式（按需追加 parser）。

## 7. 验证方式

1. 上传含「多级标题 + 表格 + 图片」的 PDF（走 MinerU；未配置时走 Docling/pypdf 降级链），
   检查 chunk 的 heading_path 注入与表格独立成块；
2. 上传 xlsx（两个 sheet、一个超长表）→ sheet 名成标题路径、长表分片重复表头；
3. 上传 docx / md / txt 各一份 → 正常入库可检索；
4. MinerU 未配置 + Docling 禁用时上传 PDF → pypdf 兜底成功；三级全失败 → 明确报错不入库；
5. 检索问表格内数据 → 命中表格 chunk 且回答带来源；
6. 单元测试：md→Block 解析、章节树切分、表头重复、碎片合并。
