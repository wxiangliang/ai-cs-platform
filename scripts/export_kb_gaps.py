"""知识缺口自动发现（Stage 37）。

    uv run python scripts/export_kb_gaps.py --tenant t1 [--days 30] [--min-count 3]
                                            [--create-drafts]

回答「系统应该知道、但现在不知道什么」。缺口信号（决策日志零埋点）：
  A. UNKNOWN+FALLBACK 轮（澄清兜底=没答上）；
  B. RAG 拒答转澄清（retrieval_json.clarify=true）；
  C. 同问法高频重复（重复=没解决）。
聚类为 v1 规则版：jieba top 关键词组 topic key。--create-drafts 把缺口
写成 faq_entry 草稿（status=draft，答案留空占位——**答案必须人工供给，
不用 LLM 生成**：缺口本来就是系统不知道的知识）。
"""

import argparse
import asyncio
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


_STOP_WORDS = {
    "我", "的", "了", "吗", "呢", "啊", "你们", "怎么", "什么", "一下",
    "请问", "帮我", "多少", "可以", "能不能", "怎么办",
}


def extract_terms(text: str) -> set[str]:
    """问句 → 关键词集合（jieba，≥2 字去停用）。"""
    import jieba

    return {
        tok for tok in jieba.cut(text or "")
        if len(tok.strip()) >= 2 and tok not in _STOP_WORDS
    }


def cluster_gaps(
    rows: list[dict[str, Any]], min_count: int = 3
) -> list[dict[str, Any]]:
    """信号轮次 → 缺口聚类（纯函数，测试锁定）。

    v1 规则聚类：**关键词重叠并桶**（与桶内词集交集 ≥2，或达较小集合的
    半数——分词边界不稳定时同话题仍能聚上；语义聚类留真实流量后换
    embedding，遗留 2）。样本数达 min_count 才算缺口（单次没答上可能是
    表达问题，重复出现才是知识缺口）。
    """
    buckets: list[dict[str, Any]] = []
    for row in rows:
        text = (row.get("text") or "").strip()
        terms = extract_terms(text)
        if not text or not terms:
            continue
        target = None
        for b in buckets:
            overlap = len(terms & b["terms"])
            # 并桶：共享 ≥2 词，或共享词达较小集合的一半（短问句容错）
            if overlap >= 2 or overlap * 2 >= min(len(terms), len(b["terms"])):
                target = b
                break
        if target is None:
            target = {"terms": set(), "count": 0, "modes": defaultdict(int),
                      "examples": [], "term_freq": defaultdict(int)}
            buckets.append(target)
        target["terms"] |= terms
        for term in terms:
            target["term_freq"][term] += 1
        target["count"] += 1
        target["modes"][row.get("mode") or "unknown"] += 1
        if len(target["examples"]) < 3 and text not in target["examples"]:
            target["examples"].append(text)

    gaps = [
        {
            # 主题展示：桶内最高频 3 个关键词
            "gap_topic": "|".join(
                t for t, _n in sorted(b["term_freq"].items(), key=lambda x: (-x[1], x[0]))[:3]
            ),
            "sample_count": b["count"],
            "failure_modes": ",".join(f"{m}:{n}" for m, n in sorted(b["modes"].items())),
            "candidate_questions": " / ".join(b["examples"]),
        }
        for b in buckets
        if b["count"] >= min_count
    ]
    gaps.sort(key=lambda g: -g["sample_count"])
    return gaps


async def collect_signals(tenant: str, days: int) -> list[dict[str, Any]]:
    """从决策日志取缺口信号轮次。"""
    from sqlalchemy import text as sql

    from app.db.session import AsyncSessionLocal

    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = sql(
        """
        SELECT COALESCE(normalized_text, original_text) AS text,
               CASE
                 WHEN retrieval_json->>'clarify' = 'true' THEN 'rag_refused'
                 WHEN status = 'FALLBACK' THEN 'unknown_fallback'
                 ELSE 'repeated'
               END AS mode
        FROM chat_decision_log
        WHERE tenant_id = :tenant AND created_at >= :since
          AND (
            status = 'FALLBACK'
            OR retrieval_json->>'clarify' = 'true'
            OR COALESCE(normalized_text, '') IN (
              SELECT normalized_text FROM chat_decision_log
              WHERE tenant_id = :tenant AND created_at >= :since
                AND COALESCE(normalized_text, '') <> ''
              GROUP BY normalized_text HAVING count(*) >= 3
            )
          )
        """
    )
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(stmt, {"tenant": tenant, "since": since})).mappings()
        return [dict(r) for r in rows]


async def create_drafts(tenant: str, gaps: list[dict[str, Any]]) -> int:
    """缺口 → faq_entry 草稿（不覆盖已有同题；答案占位待人工）。"""
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.faq_entry import FaqEntry

    created = 0
    async with AsyncSessionLocal() as session:
        for gap in gaps:
            question = gap["candidate_questions"].split(" / ")[0][:500]
            exists = (
                await session.execute(
                    select(FaqEntry).where(
                        FaqEntry.tenant_id == tenant, FaqEntry.question == question
                    )
                )
            ).first()
            if exists:
                continue
            session.add(FaqEntry(
                tenant_id=tenant,
                question=question,
                answer=f"[待补充] 知识缺口自动发现（{gap['sample_count']} 次询问，"
                       f"模式 {gap['failure_modes']}），请运营补写答案后走审核发布。",
                status="draft",
            ))
            created += 1
        await session.commit()
    return created


async def main() -> None:
    parser = argparse.ArgumentParser(description="知识缺口自动发现（Stage 37）")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--min-count", type=int, default=3)
    parser.add_argument("--out", default="data/export")
    parser.add_argument("--create-drafts", action="store_true",
                        help="缺口写成 faq_entry 草稿（答案留空待人工，进 Stage 16 审核流）")
    args = parser.parse_args()

    from app.db.session import dispose_engine

    rows = await collect_signals(args.tenant, args.days)
    gaps = cluster_gaps(rows, args.min_count)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"kb_gaps_{args.tenant}_{datetime.now(timezone.utc):%Y%m%d}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["gap_topic", "sample_count", "failure_modes", "candidate_questions"]
        )
        writer.writeheader()
        writer.writerows(gaps)
    print(f"缺口 {len(gaps)} 个（信号轮次 {len(rows)}）→ {out}")

    if args.create_drafts and gaps:
        n = await create_drafts(args.tenant, gaps)
        print(f"已生成 FAQ 草稿 {n} 条（status=draft，答案待人工补写后走审核发布）")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
