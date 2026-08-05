"""Conversation Mode Gate（Stage 30，设计见 stage-30 需求文档）。

在规则控制层之后、业务意图分类之前判对话模式（SOCIAL_ONLY/TASK_ONLY/
MIXED/OOS），v1 只接管高置信 SOCIAL_ONLY：免 LLM 二判直接闲聊回复，
解决「闲聊先花一次 LLM 判是不是闲聊、再花一次生成回答」的双调用浪费。

三条纪律：
1. **共享表示**：mode head 消费 SetFit body 的句向量（调用方传入，
   一轮只编码一次）；SetFit 重训后 mode head 必须重训（同 KNN 索引红线）；
2. **业务信号优先于闲聊信号**：接受 SOCIAL 不只看概率——含强业务关键词/
   槽位值形态/显式切换信号一律反证拦截，走原流水线（宁可少省一次二判，
   不能把退款投诉误吞成闲聊）；
3. **完全可降级**：默认关；产物缺失/加载失败/推理异常一律 fail-open
   返回 None，行为与关闭一致。

UNCERTAIN 是推理拒识状态（分数/margin 不达标），不是训练标签；
拒识轮与 TASK_ONLY/MIXED/OOS 轮 v1 都只随 intent_result 落影子证据。
"""

import json
import re
import threading
from pathlib import Path
from typing import Any

from app.chat.slots.patterns import ORDER_ID_BARE_RE, PHONE_RE
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MODE_SOCIAL_ONLY = "SOCIAL_ONLY"
MODE_TASK_ONLY = "TASK_ONLY"
MODE_MIXED = "MIXED"
MODE_OOS = "OOS"
MODE_UNCERTAIN = "UNCERTAIN"

# 强业务关键词反证：命中任何一个都不允许闲聊直通（覆盖写操作/钱务/投诉/
# 商品交易核心词——闲聊门的职责边界，不追求全，追求「误吞代价高的词必在」）
_BUSINESS_KEYWORD_RE = re.compile(
    r"退款|退货|退钱|换货|维修|取消|订单|物流|快递|发货|收货|地址|发票|"
    r"投诉|举报|赔偿|理赔|价格|多少钱|优惠|降价|便宜|库存|有货|缺货|"
    r"下单|购买|支付|付款|账单|扣款|充值|会员|优惠券"
)


class ModeGate:
    """模式门推理器（线程安全懒加载单例，产物缺失即停用）。"""

    def __init__(self, model_dir: str | None = None) -> None:
        self._dir = model_dir or settings.MODE_GATE_DIR
        self._lock = threading.Lock()
        self._loaded = False
        self._head: Any = None
        self._spec: dict[str, Any] | None = None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            base = Path(self._dir)
            if not base.is_absolute():
                # 相对路径锚定仓库根（Stage 13 纪律）
                base = Path(__file__).resolve().parents[3] / base
            try:
                spec_path = base / "mode_spec.json"
                if not spec_path.exists():
                    logger.info("mode gate disabled: %s not found", spec_path)
                    return
                self._spec = json.loads(spec_path.read_text(encoding="utf-8"))
                import joblib

                self._head = joblib.load(base / "mode_head.joblib")
                logger.info("mode gate head loaded: %s", base)
            except Exception:  # noqa: BLE001 - 加载失败停用，不打断主链路
                logger.exception("mode gate load failed, disabled")
                self._head = None
                self._spec = None

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._head is not None

    def reset_for_test(self) -> None:
        """测试专用：重置懒加载状态。"""
        self._loaded = False
        self._head = None
        self._spec = None

    def predict(self, embedding: Any) -> dict[str, Any] | None:
        """对共享句向量做模式四分类；不可用/异常返回 None（fail-open）。

        返回 {mode, score, margin, top}——score 为校准后概率
        （训练侧 Platt scaling，见 mode_gate_training.md 第 2 节）。
        """
        if not self.available:
            return None
        try:
            probs = self._head.predict_proba([embedding])[0]
            classes = list(self._head.classes_)
            ranked = sorted(zip(classes, probs), key=lambda x: x[1], reverse=True)
            top = [{"mode": m, "score": round(float(p), 4)} for m, p in ranked[:3]]
            margin = (
                round(top[0]["score"] - top[1]["score"], 4) if len(top) > 1 else 1.0
            )
            return {
                "mode": top[0]["mode"],
                "score": top[0]["score"],
                "margin": margin,
                "top": top,
            }
        except Exception:  # noqa: BLE001 - 推理异常等同未启用
            logger.warning("mode gate predict failed", exc_info=True)
            return None


def business_counter_evidence(text: str) -> list[str]:
    """业务反证扫描：返回命中的反证码列表（空=无反证）。

    原则（需求文档第 4 节）：有业务副作用可能时，业务信号优先于闲聊信号。
    「你们退款真的慢死了哈哈」含「退款」→ 拦截，走业务流水线判投诉。
    """
    codes: list[str] = []
    if _BUSINESS_KEYWORD_RE.search(text):
        codes.append("business_keyword")
    if ORDER_ID_BARE_RE.search(text) or PHONE_RE.search(text):
        codes.append("slot_value_shape")
    # 显式切换信号（「另外/顺便」）：句子在衔接业务诉求，不是纯社交
    from app.chat.state.manager import SWITCH_SIGNAL_RE

    if SWITCH_SIGNAL_RE.search(text):
        codes.append("transition_signal")
    return codes


def evaluate_social(
    mode_result: dict[str, Any], text: str, has_active_task: bool
) -> tuple[bool, list[str]]:
    """SOCIAL_ONLY 接受判定：返回 (是否直通, reason_codes)。

    组合判据不只看概率：分数线 + margin 线 + 业务反证；任务进行中用更高
    分数线（误吞插话影响补槽节奏，代价高于空闲期）。阈值为冷启动基线，
    标定口径见 mode_gate_training.md 第 4 节。
    """
    codes: list[str] = []
    if mode_result.get("mode") != MODE_SOCIAL_ONLY:
        return False, codes
    score = float(mode_result.get("score") or 0.0)
    margin = float(mode_result.get("margin") or 0.0)
    min_score = (
        settings.MODE_GATE_SOCIAL_MIN_SCORE_ACTIVE
        if has_active_task
        else settings.MODE_GATE_SOCIAL_MIN_SCORE
    )
    if score < min_score:
        codes.append("low_score")
    if margin < settings.MODE_GATE_SOCIAL_MIN_MARGIN:
        codes.append("low_margin")
    counter = business_counter_evidence(text)
    codes.extend(counter)
    if codes:
        return False, codes
    codes.append("social_high_confidence")
    if has_active_task:
        codes.append("social_hold_active_task")
    return True, codes


def evaluate_oos(mode_result: dict[str, Any] | None) -> bool:
    """OOS 能力边界回复判定（子开关默认关，影子先行）。

    高置信 OOS（「帮我写段 Python」）直接回边界话术，跳过澄清 LLM——
    确定性回复，无 Key 也可用。分数/margin 线复用 SOCIAL 直通线
    （冷启动共用一套保守线，真实分布后再拆）。
    """
    if not settings.MODE_GATE_OOS_REPLY_ENABLED or not mode_result:
        return False
    return (
        mode_result.get("mode") == MODE_OOS
        and float(mode_result.get("score") or 0.0) >= settings.MODE_GATE_SOCIAL_MIN_SCORE
        and float(mode_result.get("margin") or 0.0) >= settings.MODE_GATE_SOCIAL_MIN_MARGIN
    )


# 模块级单例
mode_gate = ModeGate()
