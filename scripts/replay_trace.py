"""决策回放 CLI（Stage 09）：按 trace_id / session_id 还原每轮决策。

    uv run python scripts/replay_trace.py --trace trace_xxx
    uv run python scripts/replay_trace.py --session <session_id> [--limit 20]

按轮打印：用户文本 → 意图（top_k/来源/置信度）→ 槽位 → 状态 → 检索/工具轨迹 → 回复。
排查线上 case 不再手写 SQL。输出脱敏（与工具层同口径）。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.chat.tools.base import mask_sensitive  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engine  # noqa: E402


def _fmt_json(data: Any, indent: str = "    ") -> str:
    """紧凑打印 JSON（脱敏后），空值返回 '-'。"""
    if not data:
        return "-"
    masked = mask_sensitive(data) if isinstance(data, dict) else data
    return "\n".join(
        indent + line
        for line in json.dumps(masked, ensure_ascii=False, indent=2).splitlines()
    )


def _print_turn(idx: int, row: dict[str, Any]) -> None:
    """打印一轮决策。"""
    intent = row["intent_result_json"] or {}
    print(f"\n{'=' * 72}")
    print(f"轮次 {idx}  |  {row['created_at']}  |  trace={row['trace_id'] or '-'}")
    print(f"{'=' * 72}")
    print(f"用户: {row['original_text']}")
    if row["normalized_text"] and row["normalized_text"] != row["original_text"]:
        print(f"归一化: {row['normalized_text']}")
    final = intent.get("final_intent") or intent.get("pred_label")
    print(
        f"意图: {final}  (来源={row['decision_source']}"
        f"  置信度={intent.get('confidence', '-')})"
    )
    if intent.get("top_k"):
        print(f"  top_k: {intent['top_k']}")
    if row["slot_result_json"]:
        print(f"槽位: {json.dumps(mask_sensitive(dict(row['slot_result_json'])), ensure_ascii=False)}")
    print(f"技能: {row['selected_skill'] or '-'}  |  状态: {row['status']}")
    trace = (row["graph_trace_json"] or {}).get("trace")
    if trace:
        print(f"图轨迹: {' -> '.join(trace)}")
    if row["latency_json"]:
        print(f"耗时: {row['latency_json']}")
    if row["retrieval_json"]:
        print("检索/工具轨迹:")
        print(_fmt_json(dict(row["retrieval_json"])))
    if row["error_json"]:
        print(f"错误: {row['error_json']}")
    if row["reply"]:
        print(f"AI 回复: {row['reply']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trace", help="trace_id（单轮）")
    group.add_argument("--session", help="session_id（整个会话按时间正序）")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    # 决策日志为主，经 message_id / (session, trace) 关联出 AI 回复文本
    base = """
        SELECT d.*, m.trace_id,
               (SELECT content FROM chat_message am
                WHERE am.session_id = d.session_id AND am.role IN ('assistant', 'agent')
                  AND am.trace_id = m.trace_id
                ORDER BY am.created_at DESC LIMIT 1) AS reply
        FROM chat_decision_log d
        LEFT JOIN chat_message m ON m.id = d.message_id
    """
    if args.trace:
        sql = text(base + " WHERE m.trace_id = :v ORDER BY d.created_at LIMIT :limit")
    else:
        sql = text(base + " WHERE d.session_id = :v ORDER BY d.created_at LIMIT :limit")

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(sql, {"v": args.trace or args.session, "limit": args.limit})
        ).mappings().all()

    if not rows:
        print("未找到决策记录")
    for idx, row in enumerate(rows, 1):
        _print_turn(idx, dict(row))
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
