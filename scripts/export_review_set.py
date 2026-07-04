"""数据回流待审样本导出 CLI（Stage 09，人工审核在环）。

    uv run python scripts/export_review_set.py --tenant t1 [--days 7] [--out data/export/]
    uv run python scripts/export_review_set.py --tenant t1 --mode faq   # FAQ 沉淀候选

review 模式（默认）：从 decision_log 导出待人工改标样本——
  SETFIT_LOW_CONF / LLM 二判 / FALLBACK 轮次 / 用户差评（chat_feedback.rating=down）轮次 /
  低分 CSAT（score<=2）会话的全部轮次（Stage 15）。
  字段对齐训练集：text,intent,split,source（另附 reason/trace_id 供审核参考）。
  人工修订 intent 列后：uv run python scripts/build_intent_dataset.py --extra <文件> → 重训 → 评估门禁。

faq 模式：导出高频 RAG 已答问题（按归一化文本粗聚类计数），
  运营审核后经 POST /api/kb/faqs 入 FAQ 精确层。

一律脱敏（手机号打码）；导出文件在 data/export/（gitignore，不进 git）；幂等可重跑。
"""

import argparse
import asyncio
import csv
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.chat.tools.base import mask_sensitive  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engine  # noqa: E402

TRAIN_CSV = Path("docs/intent/intent_train_v42_project.csv")

# 待审来源：低置信 / LLM 二判（模型分歧样本）/ 规则兜底
REVIEW_SOURCES = ("SETFIT_LOW_CONF", "LLM", "SETFIT_FALLBACK_RULE", "RULE_FALLBACK")


def _load_train_texts() -> set[str]:
    """已入训练集的文本（导出排除，防重复标注）。"""
    if not TRAIN_CSV.exists():
        return set()
    with TRAIN_CSV.open(encoding="utf-8") as f:
        return {row["text"].strip() for row in csv.DictReader(f)}


def _mask(text_value: str) -> str:
    """复用工具层脱敏口径（手机号打码）。"""
    return str(mask_sensitive({"v": text_value})["v"])


async def export_review(tenant: str, days: int, out_dir: Path) -> None:
    """导出待审样本：去重（同 normalized_text 取最新）+ 排除已入训练集 + 脱敏。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT DISTINCT ON (d.normalized_text)
               d.normalized_text,
               d.intent_result_json ->> 'final_intent' AS pred_intent,
               d.intent_result_json ->> 'pred_label'   AS pred_label,
               d.decision_source, d.status, d.created_at,
               m.trace_id
        FROM chat_decision_log d
        LEFT JOIN chat_message m ON m.id = d.message_id
        WHERE d.tenant_id = :tenant AND d.created_at >= :since
          AND d.normalized_text IS NOT NULL AND d.normalized_text <> ''
          AND (
            d.decision_source = ANY(:sources)
            OR d.status = 'FALLBACK'
            -- 低分 CSAT 会话（Stage 15）：整段体验差，全部轮次进待审
            OR EXISTS (
                SELECT 1 FROM chat_csat cs
                WHERE cs.tenant_id = d.tenant_id AND cs.session_id = d.session_id
                  AND cs.score <= 2
            )
            -- 差评轮次：feedback 评的是 AI 回复，经 trace_id 关联同轮用户消息
            OR EXISTS (
                SELECT 1 FROM chat_feedback f
                JOIN chat_message fm ON fm.id = f.message_id
                WHERE f.tenant_id = d.tenant_id AND f.rating = 'down'
                  AND fm.trace_id IS NOT NULL AND fm.trace_id = m.trace_id
            )
          )
        ORDER BY d.normalized_text, d.created_at DESC
        """
    )
    train_texts = _load_train_texts()
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                sql, {"tenant": tenant, "since": since, "sources": list(REVIEW_SOURCES)}
            )
        ).mappings().all()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"review_{tenant}_{datetime.now(timezone.utc):%Y%m%d}.csv"
    kept, skipped_in_train = 0, 0
    with out_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        # text/intent/split/source 与训练集列对齐；后三列供审核参考，合并前删除
        writer.writerow(["text", "intent", "split", "source", "reason", "status", "trace_id"])
        for r in rows:
            masked = _mask(r["normalized_text"].strip())
            if masked in train_texts:
                skipped_in_train += 1
                continue
            writer.writerow([
                masked,
                r["pred_intent"] or r["pred_label"] or "META.UNKNOWN",  # 预标，人工修订
                "train",
                "review_backflow",
                r["decision_source"] or "",
                r["status"] or "",
                r["trace_id"] or "",
            ])
            kept += 1
    print(f"exported {kept} samples -> {out_file}（排除已入训练集 {skipped_in_train} 条）")


async def export_faq_candidates(tenant: str, days: int, out_dir: Path, min_count: int) -> None:
    """FAQ 沉淀候选：高频 RAG 已答问题（按归一化文本聚合计数）。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text(
        """
        SELECT normalized_text, retrieval_json
        FROM chat_decision_log
        WHERE tenant_id = :tenant AND created_at >= :since
          AND retrieval_json IS NOT NULL
          AND (retrieval_json ->> 'refused')::boolean IS NOT TRUE
          AND normalized_text IS NOT NULL AND normalized_text <> ''
        """
    )
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql, {"tenant": tenant, "since": since})).mappings().all()

    # 粗聚类：归一化文本直接计数（同义句聚类留给运营审核，宁多勿漏）
    counter: Counter[str] = Counter(_mask(r["normalized_text"].strip()) for r in rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"faq_candidates_{tenant}_{datetime.now(timezone.utc):%Y%m%d}.csv"
    kept = 0
    with out_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["question", "count", "suggested_answer"])  # answer 由运营审核补
        for question, count in counter.most_common():
            if count < min_count:
                break
            writer.writerow([question, count, ""])
            kept += 1
    print(f"exported {kept} faq candidates (count>={min_count}) -> {out_file}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--mode", choices=["review", "faq"], default="review")
    parser.add_argument("--out", default="data/export")
    parser.add_argument("--min-count", type=int, default=3, help="faq 模式的最小出现次数")
    args = parser.parse_args()

    out_dir = Path(args.out)
    if args.mode == "review":
        await export_review(args.tenant, args.days, out_dir)
    else:
        await export_faq_candidates(args.tenant, args.days, out_dir, args.min_count)
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
