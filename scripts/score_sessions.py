"""规则版会话质量 Scorecard（Stage 37）。

    uv run python scripts/score_sessions.py --tenant t1 [--days 7]

从「技术指标」升级到「客服质量指标」：逐会话从决策日志离线推导评分
（公式见 stage-37 需求第 2 节）。三标签并存（评审红线：自动分不作唯一
真值）：auto_score（本脚本规则）/ human_score（留空待人工审核）/
user_score（CSAT 映射）。
"""

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def score_session(turns: list[dict[str, Any]], csat: int | None = None,
                  feedback_down: int = 0) -> dict[str, Any]:
    """单会话规则评分（纯函数，测试锁定）。turns 每项：
    {status, pred_label, trace(list[str]), proactive_applied(bool)}。"""
    total = len(turns)
    unknown_turns = sum(
        1 for t in turns
        if t.get("status") == "FALLBACK" or t.get("pred_label") == "META.UNKNOWN"
    )
    correction_turns = sum(
        1 for t in turns
        if any(
            marker in step
            for step in (t.get("trace") or [])
            for marker in ("switch_guard", "unknown_hold", "task_denied")
        )
    )
    handoff = any(t.get("status") in ("HANDOFF", "HANDOFF_SILENT") for t in turns)
    resolved = any(t.get("status") in ("DONE", "CONFIRMED") for t in turns) and not handoff
    marketing = sum(1 for t in turns if t.get("proactive_applied"))

    score = 100
    if not resolved:
        score -= 30
    score -= min(unknown_turns * 8, 24)
    score -= min(correction_turns * 6, 18)
    if marketing > 1:
        score -= (marketing - 1) * 5
    if (csat is not None and csat <= 2) or feedback_down:
        score -= 20
    score = max(score, 0)

    return {
        "turns": total,
        "resolved": int(resolved),
        "handoff": int(handoff),
        "unknown_turns": unknown_turns,
        "correction_turns": correction_turns,
        "marketing_applied": marketing,
        "auto_score": score,
        "human_score": "",  # 待人工审核（三标签纪律）
        "user_score": csat if csat is not None else "",
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="规则版会话质量 Scorecard（Stage 37）")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out", default="data/export")
    args = parser.parse_args()

    from sqlalchemy import text as sql

    from app.db.session import AsyncSessionLocal, dispose_engine

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    stmt = sql(
        """
        SELECT d.session_id, d.status,
               d.intent_result_json->>'pred_label' AS pred_label,
               d.graph_trace_json->'trace'         AS trace,
               (d.graph_trace_json->'proactive'->>'applied')::boolean AS proactive_applied,
               cs.score AS csat,
               (SELECT count(*) FROM chat_feedback f
                 WHERE f.tenant_id = d.tenant_id AND f.session_id = d.session_id
                   AND f.rating = 'down') AS feedback_down
        FROM chat_decision_log d
        LEFT JOIN chat_csat cs
          ON cs.tenant_id = d.tenant_id AND cs.session_id = d.session_id
        WHERE d.tenant_id = :tenant AND d.created_at >= :since
        ORDER BY d.session_id, d.created_at
        """
    )
    sessions: dict[str, dict[str, Any]] = {}
    async with AsyncSessionLocal() as session:
        for row in (await session.execute(stmt, {"tenant": args.tenant, "since": since})).mappings():
            s = sessions.setdefault(
                row["session_id"], {"turns": [], "csat": None, "feedback_down": 0}
            )
            s["turns"].append({
                "status": row["status"],
                "pred_label": row["pred_label"],
                "trace": row["trace"] or [],
                "proactive_applied": bool(row["proactive_applied"]),
            })
            if row["csat"] is not None:
                s["csat"] = int(row["csat"])
            s["feedback_down"] = int(row["feedback_down"] or 0)
    await dispose_engine()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"scorecard_{args.tenant}_{datetime.now(timezone.utc):%Y%m%d}.csv"
    fields = ["session_id", "turns", "resolved", "handoff", "unknown_turns",
              "correction_turns", "marketing_applied", "auto_score",
              "human_score", "user_score"]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for session_id, s in sessions.items():
            writer.writerow({
                "session_id": session_id,
                **score_session(s["turns"], s["csat"], s["feedback_down"]),
            })
    avg = (
        sum(score_session(s["turns"], s["csat"], s["feedback_down"])["auto_score"]
            for s in sessions.values()) / len(sessions)
        if sessions else 0
    )
    print(f"评分 {len(sessions)} 个会话（均分 {avg:.1f}）→ {out}")


if __name__ == "__main__":
    asyncio.run(main())
