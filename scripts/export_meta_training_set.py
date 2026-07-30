"""Meta-classifier 真实训练数据导出 CLI（Stage 27，操作文档第 7 节）。

    uv run python scripts/export_meta_training_set.py --tenant t1 [--days 30] [--out data/export/]

从 chat_decision_log 导出影子模式采集的特征向量（graph_trace_json.meta_shadow.features），
生成与合成训练集**同契约**的 CSV——可直接喂给 train_meta_classifier.py：

    uv run python scripts/train_meta_classifier.py --data data/export/meta_train_t1_xxx.csv

标签口径：target_decision = 链路实际决策（map_actual_decision 近似口径）＝**弱标签**。
冷启动风险：弱标签就是 Stage 26 阈值的决策，不改标直接训=克隆老师连错误一起学；
人工审核纪律（改标后再训才有超越现有阈值的可能）：
  1. **最优先审 hindsight_signal 非空行**——系统事后自己暴露决策错误的证据：
     task_deny（该轮之后同会话出现任务中途否定→之前开的任务大概率是错的）/
     handoff（之后转了人工）/ low_csat（会话 CSAT<=2）/ feedback_down（会话有差评）；
  2. 其次审 shadow_agree=False 行（模型与链路分歧样本，信息量最大）；
  3. message 列已脱敏，仅供审核参考，训练侧在泄漏黑名单内。
注意：hindsight/分歧只影响审核优先级；sample_weight 的加权（分歧行 1.5）
建立在「该行已经人工确认过标签」的前提上——未审核的行别直接进训练。

split 按 session 分组确定性分桶（md5，同会话同桶——组安全防近重复泄漏，
与合成集 case_family 纪律一致）；导出文件在 data/export/（gitignore）。
"""

import argparse
import asyncio
import csv
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

# 与训练脚本同契约（单一事实来源在 train_meta_classifier.py 顶部常量）
_FEATURE_COLUMNS = [
    "current_state", "active_intent", "active_domain", "pending_slot",
    "slot_match_type", "slot_value_type", "setfit_top1_label", "setfit_top2_label",
    "has_active_task", "slot_match", "explicit_switch_signal",
    "has_business_object", "setfit_low_conf", "setfit_ambiguous",
    "suspended_task_count", "setfit_top1_score", "setfit_top2_score", "setfit_margin",
]
_BOOL_COLUMNS = frozenset(
    {
        "has_active_task", "slot_match", "explicit_switch_signal",
        "has_business_object", "setfit_low_conf", "setfit_ambiguous",
    }
)
def _split_for_session(session_id: str) -> str:
    """按 session 确定性分桶（80/10/10）：同会话同桶，防近重复跨集泄漏。

    用 md5 不用内置 hash（PYTHONHASHSEED 随机化会破坏可复现性，
    与 app/experiments/resolver.py 同理由）。
    """
    bucket = int(hashlib.md5(session_id.encode()).hexdigest()[:8], 16) % 100
    if bucket < 80:
        return "train"
    return "validation" if bucket < 90 else "test"


def hindsight_signal(
    task_deny: bool, handoff: bool, low_csat: bool, feedback_down: bool
) -> str:
    """把四个后见信号拼成审核参考列（逗号分隔，空串=无信号）。

    信号语义（真值均代表「该轮决策事后被证伪的概率高」，审核最优先）：
    task_deny/handoff 为轮后事件（同会话该轮之后发生），
    low_csat/feedback_down 为会话级结局。
    """
    parts = []
    if task_deny:
        parts.append("task_deny")
    if handoff:
        parts.append("handoff")
    if low_csat:
        parts.append("low_csat")
    if feedback_down:
        parts.append("feedback_down")
    return ",".join(parts)


def build_export_row(
    row_id: int,
    session_id: str,
    message: str,
    shadow: dict[str, Any],
    hindsight: str = "",
) -> dict[str, Any] | None:
    """把一条决策日志的 meta_shadow 记录转成训练契约行；缺特征返回 None。

    布尔列必须写 "True"/"False" 字符串（训练侧 build_frame 按 =="True" 解析）。
    """
    features = shadow.get("features") or {}
    if any(c not in features for c in _FEATURE_COLUMNS):
        return None
    row: dict[str, Any] = {
        "row_id": row_id,
        "split": _split_for_session(session_id),
        "case_family_id": f"session:{session_id}",
        "scenario_family": "PRODUCTION",
        "message": message,
        "control_result": "NONE",  # 影子只在语义层轮次采集，天然部署域
        "target_decision": shadow.get("actual") or "",
        "sample_weight": 1.5 if shadow.get("agree") is False else 1.0,
        "feature_source": "PRODUCTION_LOG",
        # 审核参考列（非训练输入）
        "shadow_decision": shadow.get("decision") or "",
        "shadow_agree": "" if shadow.get("agree") is None else str(shadow["agree"]),
        "hindsight_signal": hindsight,
    }
    for col in _FEATURE_COLUMNS:
        value = features[col]
        row[col] = str(bool(value)) if col in _BOOL_COLUMNS else value
    return row if row["target_decision"] else None


async def export(tenant: str, days: int, out_dir: Path) -> None:
    """导出近 N 天带影子采集记录的语义层轮次。"""
    from app.db.session import AsyncSessionLocal, dispose_engine

    since = datetime.now(timezone.utc) - timedelta(days=days)
    # 后见信号（审核优先级依据）：task_deny/handoff 只看该轮**之后**的同会话事件
    # （之前的事件证伪不了本轮决策）；CSAT/差评是会话级结局（口径同 export_review_set）
    sql = text(
        """
        SELECT d.session_id,
               COALESCE(d.normalized_text, '')       AS message,
               d.graph_trace_json -> 'meta_shadow'   AS shadow,
               EXISTS (
                   SELECT 1 FROM chat_decision_log x
                   WHERE x.tenant_id = d.tenant_id AND x.session_id = d.session_id
                     AND x.created_at > d.created_at
                     AND x.decision_source = 'RULE_TASK_DENY'
               ) AS sig_task_deny,
               EXISTS (
                   SELECT 1 FROM chat_decision_log x
                   WHERE x.tenant_id = d.tenant_id AND x.session_id = d.session_id
                     AND x.created_at > d.created_at
                     AND x.status = 'HANDOFF'
               ) AS sig_handoff,
               EXISTS (
                   SELECT 1 FROM chat_csat cs
                   WHERE cs.tenant_id = d.tenant_id AND cs.session_id = d.session_id
                     AND cs.score <= 2
               ) AS sig_low_csat,
               EXISTS (
                   SELECT 1 FROM chat_feedback f
                   WHERE f.tenant_id = d.tenant_id AND f.session_id = d.session_id
                     AND f.rating = 'down'
               ) AS sig_feedback_down
        FROM chat_decision_log d
        WHERE d.tenant_id = :tenant
          AND d.created_at >= :since
          AND d.graph_trace_json ? 'meta_shadow'
        ORDER BY d.created_at
        """
    )
    async with AsyncSessionLocal() as session:
        rows = (
            (await session.execute(sql, {"tenant": tenant, "since": since}))
            .mappings()
            .all()
        )
    await dispose_engine()

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"meta_train_{tenant}_{datetime.now(timezone.utc):%Y%m%d}.csv"
    fieldnames = [
        "row_id", "split", "case_family_id", "scenario_family", "message",
        *_FEATURE_COLUMNS, "control_result", "target_decision", "sample_weight",
        "feature_source", "shadow_decision", "shadow_agree", "hindsight_signal",
    ]
    count = disagree = flagged = 0
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in rows:
            built = build_export_row(
                count + 1,
                record["session_id"],
                record["message"],
                record["shadow"] or {},
                hindsight=hindsight_signal(
                    record["sig_task_deny"], record["sig_handoff"],
                    record["sig_low_csat"], record["sig_feedback_down"],
                ),
            )
            if built is None:
                continue
            writer.writerow(built)
            count += 1
            if built["shadow_agree"] == "False":
                disagree += 1
            if built["hindsight_signal"]:
                flagged += 1
    print(
        f"导出 {count} 行 → {out}"
        f"（后见信号 {flagged} 行【最优先审】，影子分歧 {disagree} 行【其次】）"
    )
    print("人工改标 target_decision 后重训：")
    print(f"  uv run python scripts/train_meta_classifier.py --data {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Meta-classifier 真实训练数据导出")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", default="data/export")
    args = parser.parse_args()
    asyncio.run(export(args.tenant, args.days, Path(args.out)))


if __name__ == "__main__":
    main()
