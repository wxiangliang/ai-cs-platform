"""Meta-classifier 真实训练数据导出 CLI（Stage 27，操作文档第 7 节）。

    uv run python scripts/export_meta_training_set.py --tenant t1 [--days 30] [--out data/export/]

从 chat_decision_log 导出影子模式采集的特征向量（graph_trace_json.meta_shadow.features），
生成与合成训练集**同契约**的 CSV——可直接喂给 train_meta_classifier.py：

    uv run python scripts/train_meta_classifier.py --data data/export/meta_train_t1_xxx.csv

标签三列纪律（2026-08-05 评审固化——防「模型只学会克隆现有阈值」）：
  policy_decision   链路当时实际做了什么（map_actual_decision 近似口径）——
                    **不可改的行为日志**，审核后仍保留供「策略 vs 人工」对比；
  reviewed_decision 人工认为应该做什么——**审核只填这一列**（维持原判也要
                    把原值填进来=显式确认，与未审核行可区分）；
  target_decision   训练标签列（契约兼容），导出时初始 = policy_decision；
                    训练脚本按优先级取 reviewed_decision > target_decision。
冷启动风险：弱标签就是 Stage 26 阈值的决策，不改标直接训=克隆老师连错误一起学。

人工审核优先级（hindsight_tier 分级——后见信号只排审核优先级，**永不直接当真值**：
「之后转了人工」不等于该轮意图决策错，可能是工具失败/回复质量差/用户坚持转人工）：
  strong  task_deny（该轮之后同会话出现任务中途否定=用户明确纠错，之前开的任务大概率错）
  medium  low_csat（会话 CSAT<=2）/ feedback_down（会话有差评）——会话结局差的间接证据
  weak    handoff（之后转了人工）——归因高度不确定
  其次审 shadow_agree=False 行（模型与链路分歧样本，信息量最大）；
  message 列已脱敏，仅供审核参考，训练侧在泄漏黑名单内。
注意：sample_weight 的加权（分歧行 1.5）建立在「该行已经人工确认过标签」的
前提上——未审核的行别直接进训练（训练侧 --reviewed-only 可强制执行该纪律）。

split 两种模式（--split-by，均整会话粒度=组安全，与合成集 case_family 纪律一致）：
  session（默认）md5 确定性分桶 80/10/10，同会话同桶防近重复泄漏；
  time           按会话首轮时间排序，旧 80% 会话训练、新 20% 验证/测试——
                 更接近真实上线效果，能暴露意图分布与用户表达随时间的漂移
                 （接管门禁要求时间切分评估通过，见 stage-27 文档 4.5 阶段 B）。
导出文件在 data/export/（gitignore）。
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


def _split_by_time(ordered_sessions: list[str]) -> dict[str, str]:
    """时间切分（整会话粒度=组安全）：按会话首轮时间序，前 80% 会话 train、
    其后 10% validation、最新 10% test——训练用旧数据、评估用新数据，
    更接近真实上线表现（意图分布/表达漂移会被暴露而不是被随机切分抹平）。
    """
    n = len(ordered_sessions)
    mapping: dict[str, str] = {}
    for i, sid in enumerate(ordered_sessions):
        frac = i / n
        mapping[sid] = "train" if frac < 0.8 else ("validation" if frac < 0.9 else "test")
    return mapping


def hindsight_tier(signal: str) -> str:
    """后见信号证据强度分级（审核排序用，空串=无信号）。

    strong：task_deny——用户明确纠错，最接近「该轮决策确实错了」；
    medium：low_csat / feedback_down——会话结局差的间接证据；
    weak  ：仅 handoff——转人工归因高度不确定（工具失败/回复质量/用户坚持
            都会转），**绝不能写成「转人工=当前决策错误」直接改标**。
    """
    if not signal:
        return ""
    parts = set(signal.split(","))
    if "task_deny" in parts:
        return "strong"
    if parts & {"low_csat", "feedback_down"}:
        return "medium"
    return "weak"


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
    split: str | None = None,
) -> dict[str, Any] | None:
    """把一条决策日志的 meta_shadow 记录转成训练契约行；缺特征返回 None。

    布尔列必须写 "True"/"False" 字符串（训练侧 build_frame 按 =="True" 解析）；
    split 传入则用调用方口径（时间切分），否则按 session md5 分桶。
    """
    features = shadow.get("features") or {}
    if any(c not in features for c in _FEATURE_COLUMNS):
        return None
    actual = shadow.get("actual") or ""
    row: dict[str, Any] = {
        "row_id": row_id,
        "split": split or _split_for_session(session_id),
        "case_family_id": f"session:{session_id}",
        "scenario_family": "PRODUCTION",
        "message": message,
        "control_result": "NONE",  # 影子只在语义层轮次采集，天然部署域
        # 三列标签纪律（见模块 docstring）：policy 不可改、reviewed 待人工、
        # target 初始=policy（训练侧 reviewed 优先）
        "policy_decision": actual,
        "reviewed_decision": "",
        "target_decision": actual,
        "sample_weight": 1.5 if shadow.get("agree") is False else 1.0,
        "feature_source": "PRODUCTION_LOG",
        # 审核参考列（非训练输入）
        "shadow_decision": shadow.get("decision") or "",
        "shadow_agree": "" if shadow.get("agree") is None else str(shadow["agree"]),
        "hindsight_signal": hindsight,
        "hindsight_tier": hindsight_tier(hindsight),
    }
    for col in _FEATURE_COLUMNS:
        value = features[col]
        row[col] = str(bool(value)) if col in _BOOL_COLUMNS else value
    return row if row["target_decision"] else None


async def export(tenant: str, days: int, out_dir: Path, split_by: str = "session") -> None:
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

    # 时间切分：rows 已按 created_at 升序，取每会话首次出现顺序整会话分桶
    split_map: dict[str, str] | None = None
    if split_by == "time":
        ordered: list[str] = []
        seen: set[str] = set()
        for record in rows:
            sid = record["session_id"]
            if sid not in seen:
                seen.add(sid)
                ordered.append(sid)
        split_map = _split_by_time(ordered)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"meta_train_{tenant}_{datetime.now(timezone.utc):%Y%m%d}.csv"
    fieldnames = [
        "row_id", "split", "case_family_id", "scenario_family", "message",
        *_FEATURE_COLUMNS, "control_result", "policy_decision", "reviewed_decision",
        "target_decision", "sample_weight", "feature_source",
        "shadow_decision", "shadow_agree", "hindsight_signal", "hindsight_tier",
    ]
    count = disagree = 0
    tiers = {"strong": 0, "medium": 0, "weak": 0}
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
                split=split_map[record["session_id"]] if split_map else None,
            )
            if built is None:
                continue
            writer.writerow(built)
            count += 1
            if built["shadow_agree"] == "False":
                disagree += 1
            if built["hindsight_tier"]:
                tiers[built["hindsight_tier"]] += 1
    print(
        f"导出 {count} 行 → {out}（split={split_by}）\n"
        f"审核优先级：hindsight strong {tiers['strong']} 行 > medium {tiers['medium']} 行"
        f" > 影子分歧 {disagree} 行 > weak {tiers['weak']} 行 > 其余抽检"
    )
    print("人工审核：只填 reviewed_decision 列（维持原判也填=显式确认），改完重训：")
    print(f"  uv run python scripts/train_meta_classifier.py --data {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Meta-classifier 真实训练数据导出")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", default="data/export")
    parser.add_argument(
        "--split-by", choices=["session", "time"], default="session",
        help="session=md5 分桶（默认）；time=整会话时间切分（旧训新测，接管评估用）",
    )
    args = parser.parse_args()
    asyncio.run(export(args.tenant, args.days, Path(args.out), args.split_by))


if __name__ == "__main__":
    main()
