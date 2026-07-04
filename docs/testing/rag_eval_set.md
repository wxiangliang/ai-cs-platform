# RAG 检索评估集（v1，32 组）

> 状态：✅ 已落地（2026-07-03）。标准语料见 `tests/eval/rag_corpus.py`（8 篇文档 + 6 条 FAQ，
> 评估租户 `rag-eval`），执行门禁见 `tests/eval/test_rag_eval.py`（本文件的用例表是其机器可读源）。
> 运行条件：`KB_ENABLED=true` 且 Milvus 可达，否则显式 skip。
>
> **阈值双档**：`EMBEDDING_PROVIDER=hash`（开发模式）下向量路不可靠，检索主要靠关键词路，
> 门禁按低档（命中率 ≥0.75 / 拒答准确率 ≥0.80）；接入真实 embedding 后按高档
> （≥0.90 / ≥0.90）重新校准，并补充更多改写/同义句用例（本文件 4 节 backlog）。

## 1. 评估口径

- **faq 类**：期望 FAQ 精确层命中（`answer.source == "faq"` 且答案含期望关键词）；
- **doc 类**：期望文档层回答且引用含期望文档（`citations` 含《期望标题》）；
  未达 FAQ 阈值但落入 doc 层回答也算命中（层级降级不算错）；
- **refuse 类**：知识库外问题，期望拒答（`answer is None`），**宁缺勿编红线**；
- **route 类**：价格/库存等商品红线问题，正确行为是**路由层不进 RAG**（走商品库），
  不在本 harness 断言范围（见 `tests/stage06`* 路由用例），列出仅为口径完整。

## 2. 用例表（harness 解析本表执行，勿改列结构）

| id | type | query | expect |
|---|---|---|---|
| F1 | faq | 七天无理由退货怎么申请 | 申请退货 |
| F2 | faq | 退款多久到账 | 3 个工作日 |
| F3 | faq | 满多少钱包邮 | 99 |
| F4 | faq | 保修期是多久 | 12 个月 |
| F5 | faq | 积分怎么用 | 100 积分抵 1 元 |
| F6 | faq | 可以开专票吗 | 专用发票 |
| D1 | doc | 什么商品不支持无理由退货 | 退换货政策 |
| D2 | doc | 换货需要承担运费吗 | 退换货政策 |
| D3 | doc | 退货之后钱怎么退回来 | 退换货政策 |
| D4 | doc | 人为损坏可以保修吗 | 保修政策 |
| D5 | doc | 电池的保修期限是多久 | 保修政策 |
| D6 | doc | 过保之后维修怎么收费 | 保修政策 |
| D7 | doc | 偏远地区运费加收多少 | 运费与配送 |
| D8 | doc | 预售商品什么时候发货 | 运费与配送 |
| D9 | doc | 收货时外包装破损怎么办 | 运费与配送 |
| D10 | doc | 发票抬头开错了能换开吗 | 发票政策 |
| D11 | doc | 下单后多久内可以补开发票 | 发票政策 |
| D12 | doc | 金卡会员需要消费满多少 | 会员与积分规则 |
| D13 | doc | 积分会过期吗 | 会员与积分规则 |
| D14 | doc | 买贵了可以退差价吗 | 价格保护政策 |
| D15 | doc | 价保可以申请几次 | 价格保护政策 |
| D16 | doc | 信用卡分期支持几期 | 支付方式说明 |
| D17 | doc | 账号注销后积分还在吗 | 账号与隐私政策 |
| D18 | doc | 支付成功了订单没更新怎么办 | 支付方式说明 |
| R1 | refuse | 今天上证指数涨了多少 | REFUSE |
| R2 | refuse | 帮我写一份租房合同 | REFUSE |
| R3 | refuse | 隔壁平台的会员价是多少 | REFUSE |
| R4 | refuse | 明天北京天气怎么样 | REFUSE |
| R5 | refuse | 你们公司股票代码是什么 | REFUSE |
| P1 | route | 这款手机多少钱 | PRODUCT（价格禁走 RAG） |
| P2 | route | XPhone 15 还有货吗 | PRODUCT（库存禁走 RAG） |
| P3 | route | 这个型号有什么配置参数 | PRODUCT（商品库优先，RAG 仅增强） |

## 3. 运行方式

```bash
# 播种语料（首次自动）并执行门禁；无 Milvus / KB_ENABLED=false 时显式 skip
uv run pytest tests/eval/test_rag_eval.py -q -rs
```

harness 行为：
1. 评估租户 `rag-eval` 无文档时自动播种 `rag_corpus.py`（幂等，不重复入库）；
2. hash 模式自动降阈值（FAQ_HIT_THRESHOLD=0.6 / RAG_MIN_SCORE=0.2，与 runbook 开发模式口径一致）；
3. 输出逐条命中明细与两项指标：`hit_rate`（faq+doc 类）与 `refusal_acc`（refuse 类）。

## 4. 接入真实 embedding 后的 backlog

- 高档门禁（hit ≥0.90 / refusal ≥0.90），重校 FAQ_HIT_THRESHOLD / RAG_MIN_SCORE 生产值；
- 补充改写句/错别字/繁体变体用例（每篇文档 ≥1 条同义改写）；
- 补充歧义检测用例（同主题跨文档冲突 → 摘录附提示，Stage 06-04 已实现能力）；
- 表格类事实问答用例（结构感知切分的大表分片场景）。
