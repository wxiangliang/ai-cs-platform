"""Meta-classifier 影子模式（Stage 27 阶段 A，见 stage-27 文档 4.5）。

对每轮**语义层**决策（控制层短路的轮次不在部署域）用离线训练的
Meta-classifier 做一次预测：落决策日志（graph_trace_json.meta_shadow）、
出分歧率指标（meta_shadow_total{decision,agree}），**不驱动任何决策**——
合成数据训出的模型不碰生产决策是红线；影子期积累的分歧样本正是
未来接管评估（以及真实特征表重训）的数据来源。

降级纪律（全部 fail-open，任何异常不打断主链路）：
- 模型产物缺失（models/ 不进镜像）→ 预测层停用，**特征采集照常**
  （特征+实际决策是真实训练数据的回流通道，不因产物缺失中断）；
- pandas/joblib 不可用、推理异常 → 只丢预测不丢采集；特征构造异常 → 本轮跳过；
- 推理是 CPU 上的单行预测（LR <1ms / LGBM 数 ms），同步内联执行。

特征契约以 scripts/train_meta_classifier.py 顶部常量为单一事实来源，
本模块按 feature_spec.json（训练产物）构造同构输入；训练数据取值域与
运行时信号的映射（如 pending_slot → customer_phone_or_order_id）在此收口。
"""

import json
import threading
from pathlib import Path
from typing import Any

from app.chat.intent.types import DecisionSource, IntentLabel
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import count_meta_shadow

logger = get_logger(__name__)

# 部署域来源：控制层（RULE_KEYWORD/CONFIRM_GATE/SLOT_ONLY/PENDING_SLOT/TASK_DENY）
# 短路的轮次运行时到不了 Meta 层，与训练域 control_result==NONE 对齐；
# KNN 确认来源也是语义层决策（margin 难例的免二判采纳），属部署域
_SHADOW_SOURCES = frozenset(
    {
        DecisionSource.SETFIT,
        DecisionSource.SETFIT_LOW_CONF,
        DecisionSource.SETFIT_LOW_MARGIN,
        DecisionSource.SETFIT_KNN_CONFIRMED,
        DecisionSource.SETFIT_KNN_CHITCHAT,
        DecisionSource.LLM,
        DecisionSource.RULE_FALLBACK,
    }
)

# 运行时槽位名 → 训练数据取值域映射（未映射的值原样传入，
# OneHot handle_unknown=ignore / LGBM 未见类别按缺失处理，安全）
_PENDING_SLOT_MAP = {"order_id": "customer_phone_or_order_id", "phone": "customer_phone_or_order_id"}
_EVIDENCE_MAP = {
    "explicit_slot_name": "EXPLICIT_SLOT_NAME",
    "contextual_answer": "CONTEXTUAL_ANSWER",
    "pure_value": "SEMANTIC_SLOT",
}
_NON_BUSINESS_DOMAINS = frozenset({"META", "CHITCHAT"})


class _ShadowModel:
    """懒加载单例：模型 + 特征契约。加载失败即停用（进程内只试一次）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self._model: Any = None
        self._spec: dict[str, Any] | None = None
        self._model_name = ""

    def _load(self) -> None:
        base = Path(settings.META_SHADOW_DIR)
        if not base.is_absolute():
            # 相对路径锚定仓库根（Stage 13 纪律）：任意 CWD 启动都能找到产物
            base = Path(__file__).resolve().parents[3] / base
        try:
            spec_path = base / "feature_spec.json"
            if not spec_path.exists():
                logger.info("meta shadow disabled: %s not found", spec_path)
                return
            self._spec = json.loads(spec_path.read_text(encoding="utf-8"))
            name = settings.META_SHADOW_MODEL
            if name == "champion":
                metrics = json.loads((base / "metrics.json").read_text(encoding="utf-8"))
                name = metrics.get("champion") or "lr"
            import joblib

            self._model = joblib.load(base / f"model_{name}.joblib")
            self._model_name = name
            logger.info("meta shadow model loaded: %s", name)
        except Exception:  # noqa: BLE001 - 影子模式加载失败只停用，不打断启动/主链路
            logger.exception("meta shadow load failed, disabled")
            self._model = None
            self._spec = None

    def get(self) -> tuple[Any, dict[str, Any], str] | None:
        if not self._loaded:
            with self._lock:
                if not self._loaded:
                    self._load()
                    self._loaded = True
        if self._model is None or self._spec is None:
            return None
        return self._model, self._spec, self._model_name


_shadow = _ShadowModel()


def reset_for_test() -> None:
    """测试专用：重置懒加载状态。"""
    global _shadow
    _shadow = _ShadowModel()


# ---------------------------------------------------------------------------
# 特征构造（与 feature_spec.json 同构）
# ---------------------------------------------------------------------------

def build_features(state: dict[str, Any], intent_dict: dict[str, Any]) -> dict[str, Any]:
    """从图 state（resolve 前的任务上下文）+ 意图结果构造单行特征。

    与训练特征契约一一对应；has_business_object 为运行时近似
    （业务域意图或抽到槽位即视为带业务对象，stage-27 遗留 2）。
    """
    task = state.get("active_task") or {}
    collected = task.get("collected_slots") or {}
    active_intent = task.get("intent") or ""
    pending_slot = ""
    for s in task.get("required_slots", []):
        if not collected.get(s):
            pending_slot = s
            break

    top_k = intent_dict.get("top_k") or []
    top1 = top_k[0] if top_k else {}
    top2 = top_k[1] if len(top_k) > 1 else {}
    top1_label = top1.get("label") or intent_dict.get("pred_label") or "META.UNKNOWN"
    top1_score = float(top1.get("score") or intent_dict.get("confidence") or 0.0)
    top2_label = top2.get("label") or "NONE"
    top2_score = float(top2.get("score") or 0.0)
    margin = intent_dict.get("margin")
    margin = float(margin) if margin is not None else round(top1_score - top2_score, 4)

    fill = intent_dict.get("pending_fill") or {}
    slot_match = bool(fill.get("slot"))
    slots = state.get("slots") or {}
    top1_domain = top1_label.split(".")[0] if "." in top1_label else top1_label

    from app.chat.state.manager import SWITCH_SIGNAL_RE

    low_conf_line = (
        settings.INTENT_CONFIDENCE_THRESHOLD_WRITE
        if top1_domain in ("AFTERSALE", "ORDER", "PAYMENT")
        else settings.INTENT_CONFIDENCE_THRESHOLD
    )
    return {
        "current_state": state.get("current_state") or "IDLE",
        "active_intent": active_intent or "NONE",
        "active_domain": (active_intent.split(".")[0] if active_intent else "NONE"),
        "pending_slot": _PENDING_SLOT_MAP.get(pending_slot, pending_slot) or "NONE",
        "slot_match_type": _EVIDENCE_MAP.get(fill.get("evidence", ""), "NONE"),
        "slot_value_type": (
            "ORDER_ID_OR_PHONE" if fill.get("slot") in ("order_id", "phone") else "NONE"
        ),
        "setfit_top1_label": top1_label,
        "setfit_top2_label": top2_label,
        "has_active_task": 1 if task else 0,
        "slot_match": 1 if slot_match else 0,
        "explicit_switch_signal": (
            1 if SWITCH_SIGNAL_RE.search(state.get("normalized_text") or "") else 0
        ),
        "has_business_object": (
            1 if (slots or top1_domain not in _NON_BUSINESS_DOMAINS) else 0
        ),
        "setfit_low_conf": 1 if top1_score < low_conf_line else 0,
        "setfit_ambiguous": 1 if margin < settings.INTENT_MIN_MARGIN else 0,
        "suspended_task_count": float(len(state.get("task_stack") or [])),
        "setfit_top1_score": top1_score,
        "setfit_top2_score": top2_score,
        "setfit_margin": margin,
    }


def derive_reason_codes(
    features: dict[str, Any], intent_dict: dict[str, Any]
) -> list[str]:
    """从证据派生原因码（2026-08-05 决策融合层评审采纳项）。

    目的：审核/分歧分析不用人肉翻 JSON 推原因——「为什么这轮是难例/
    为什么像切换」直接可读可 SQL 聚合。纯派生（证据的确定函数），
    不新增信息、不参与任何决策；码表按需扩展，改动同步本函数与测试。
    """
    codes: list[str] = []
    source = intent_dict.get("decision_source")
    if source == DecisionSource.LLM:
        codes.append("llm_second_opinion")
    if source == DecisionSource.SETFIT_KNN_CONFIRMED:
        codes.append("knn_confirmed_skip_llm")
    if source == DecisionSource.SETFIT_KNN_CHITCHAT:
        codes.append("knn_chitchat_rescue")
    if features.get("setfit_low_conf"):
        codes.append("low_confidence")
    if features.get("setfit_ambiguous"):
        codes.append("low_margin")
    # KNN 交叉验证与 top1 的一致性（开启 KNN 后才有该证据）
    knn = intent_dict.get("example_knn")
    if knn:
        top1 = features.get("setfit_top1_label")
        codes.append(
            "knn_agrees_top1" if knn.get("label") == top1 else "knn_disagrees_top1"
        )
    # 任务上下文：进行中任务 + 新意图与其不同 = 潜在切换轮
    if features.get("has_active_task"):
        codes.append("has_active_task")
        active = features.get("active_intent")
        if active not in (None, "", "NONE") and active != features.get("setfit_top1_label"):
            codes.append("differs_from_active_task")
    if features.get("explicit_switch_signal"):
        codes.append("explicit_switch_signal")
    if features.get("suspended_task_count", 0):
        codes.append("has_suspended_tasks")
    return codes


def map_actual_decision(state: dict[str, Any], result_state: dict[str, Any]) -> str:
    """把链路实际结局映射到 6 类决策码（分歧率的对照口径，**近似**）。

    口径记录（分析 SQL 引用时以此为准）：
    - 切换守护拦截 / UNKNOWN 无证据保持 → CONTINUE_CURRENT（hold 在当前任务）；
    - LLM 二判来源 → SEND_TO_LLM（系统把该轮送了二判）；
    - UNKNOWN：有任务续接 → CONTINUE_CURRENT，无任务 → UNKNOWN
      （链路走 Stage 21 澄清，与 ASK_CLARIFICATION 的边界真实化后再细分）；
    - 业务意图：原任务被挂起/替换 → SWITCH_NEW，此前无进行中任务 → ACCEPT_NEW_INTENT。
    """
    intent_dict = state.get("intent_result") or {}
    pred = intent_dict.get("pred_label") or ""
    if result_state.get("switch_candidate") or result_state.get("unknown_with_task"):
        return "CONTINUE_CURRENT"
    if intent_dict.get("decision_source") == DecisionSource.LLM:
        return "SEND_TO_LLM"
    if pred == IntentLabel.META_UNKNOWN:
        return "CONTINUE_CURRENT" if state.get("active_task") else "UNKNOWN"
    had_task = bool(state.get("active_task"))
    if had_task and state.get("current_state") in ("COLLECTING", "CONFIRMING"):
        new_task = result_state.get("active_task") or {}
        if new_task.get("intent") == pred != (state.get("active_task") or {}).get("intent"):
            return "SWITCH_NEW"
        return "CONTINUE_CURRENT"
    return "ACCEPT_NEW_INTENT"


# ---------------------------------------------------------------------------
# 入口（dialog_state_resolve 节点调用）
# ---------------------------------------------------------------------------

def shadow_predict(
    state: dict[str, Any], result_state: dict[str, Any]
) -> dict[str, Any] | None:
    """影子采集 + 预测一轮；不在部署域/未启用时返回 None（零副作用）。

    分两层，各自独立降级：
    1. **特征采集（不依赖模型产物）**：特征向量 + 实际决策（弱标签）每轮
       随决策日志落库——上线后积累真实训练数据的通道，由
       scripts/export_meta_training_set.py 导出重训（操作文档第 7 节）；
    2. 模型预测（产物在位时）：附加 decision/agree/model 并计分歧率指标。
    """
    if not settings.META_SHADOW_ENABLED or state.get("blocked"):
        return None
    intent_dict = state.get("intent_result") or {}
    if intent_dict.get("decision_source") not in _SHADOW_SOURCES:
        return None
    try:
        features = build_features(state, intent_dict)
        record: dict[str, Any] = {
            "features": features,
            "actual": map_actual_decision(state, result_state),
            "reason_codes": derive_reason_codes(features, intent_dict),
        }
    except Exception:  # noqa: BLE001 - 影子失败绝不打断主链路
        logger.warning("meta shadow feature build failed", exc_info=True)
        return None

    loaded = _shadow.get()
    if loaded is None:
        return record  # 无模型也保留特征采集（训练数据回流不因产物缺失中断）
    model, spec, model_name = loaded
    try:
        import pandas as pd
        from sklearn.pipeline import Pipeline

        frame = pd.DataFrame([features])
        # LGBM/XGB 需要与训练一致的 category dtype（未见类别→NaN 按缺失处理）；
        # sklearn 管道（lr/tree）保留原始字符串，OneHot handle_unknown=ignore 自会处理
        if not isinstance(model, Pipeline):
            for col, cats in (spec.get("categorical") or {}).items():
                frame[col] = pd.Categorical([features[col]], categories=cats)
        if isinstance(model, tuple):  # xgb 产物是 (model, LabelEncoder)
            clf, enc = model
            decision = str(enc.inverse_transform(clf.predict(frame))[0])
        else:
            decision = str(model.predict(frame)[0])
        agree = decision == record["actual"]
        count_meta_shadow(decision, agree)
        record.update({"decision": decision, "agree": agree, "model": model_name})
    except Exception:  # noqa: BLE001 - 预测失败不影响特征采集
        logger.warning("meta shadow predict failed", exc_info=True)
    return record
