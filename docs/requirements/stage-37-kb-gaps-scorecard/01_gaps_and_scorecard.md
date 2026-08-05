# Stage 37 需求：知识缺口自动发现 + 规则版质量 Scorecard

> 来源：roadmap 3.7 第四项。两件事共用同一原料——决策日志已经记录了
> 「系统答不上什么、答得怎么样」，本 Stage 把它们变成运营可消费的产物。

---

## 1. 知识缺口发现（`scripts/export_kb_gaps.py`）

回答「系统应该知道、但现在不知道什么」：

```text
缺口信号（decision_log，均已有字段零埋点）：
  A. UNKNOWN+FALLBACK 轮（澄清兜底=没答上）
  B. RAG 拒答转澄清（retrieval_json.clarify=true）
  C. 高频重复问句（同租户同问法 ≥N 次——重复=没解决）

聚类（v1 规则版）：jieba 提取问句 top 关键词组成 topic key，
同 topic 聚合 → 缺口报表 CSV：topic/样本数/失败模式分布/示例问句(3)
  → [--create-drafts] 每个缺口生成 faq_entry 草稿（status=draft，
    答案留空占位）→ 进 Stage 16 运营后台审核流 → 发布 → 拒答率回看
```

红线：**草稿答案不用 LLM 生成**（v1）——缺口的答案本来就是系统不知道
的知识，必须人工供给；脚本只负责把「该写什么」聚出来。

## 2. 规则版质量 Scorecard（`scripts/score_sessions.py`）

从「技术指标」升级到「客服质量指标」，v1 全规则可离线复算：

| 维度 | 信号（决策日志推导） | 扣分 |
|---|---|---|
| resolution | 会话含 DONE 轮且未转人工=1 | 否则 -30 |
| unnecessary_turns | UNKNOWN/澄清轮占比 | 每轮 -8（上限 -24） |
| direction_errors | switch_guard/unknown_hold/task_denied 纠偏轮 | 每轮 -6（上限 -18） |
| handoff_quality | 转人工且无上下文包=0（结构上恒有=不扣） | — |
| marketing_intrusion | 主动动作 applied 次数 >1 | 每次 -5 |
| user_signal | CSAT ≤2 或差评 | -20 |

产出 CSV 三标签并存（评审红线：**LLM/规则自动分不作唯一真值**）：
`auto_score`（本脚本）/ `human_score`（留空待人工审核）/
`user_score`（CSAT 映射）。LLM 会话级评分待真实 Key（roadmap backlog）。

## 3. 验收

1. 缺口报表：三类信号可聚出 topic、样本数与示例；`--create-drafts`
   生成 draft 态 FAQ（不覆盖已有同题）；
2. Scorecard：纯函数评分可测（满分会话/转人工/纠偏/差评样例）；
3. 两脚本对空数据/无信号租户输出空报表不报错。

## 4. 遗留

1. LLM 会话级评分 + 人工审核工作流（三标签闭环，真实 Key 后）；
2. 语义聚类（真实流量后换 embedding 聚类，v1 关键词桶够用）；
3. 缺口→坐席补答→FAQ 沉淀的自动通路（现有 export_review_set --mode faq 可用）；
4. quality_daily 加会话质量分列。
