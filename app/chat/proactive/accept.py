"""主动建议接受通道（Stage 41，需求第 3.2 节）。

补齐拒绝检测的对称面：上轮真实展示过主动建议（活动/注册引导/服务延伸）
且本轮是**纯接受**短句（「好的」「可以」「发我看看」）时，按窗口 payload 里的
accept_intent 开出普通任务——此前这类回复会掉进 SetFit 当普通消息。

三条红线（需求 3.2）：
1. **只对上轮真实展示过的建议生效**：`proactive:last` 10 分钟窗口键是唯一凭据
   （只有 PROACTIVE_APPLY 真实追加时才写入），无窗口时「好的」绝不被劫持；
2. **纯接受判定**：带业务残差（「好的，另外我要退货」）不判接受，照常走
   分类——多意图切分/语义层接手；
3. **接受 ≠ 跳确认门**：开出的是普通任务，该补槽补槽、该确认确认
   （同意看推荐 ≠ 同意下单）。判定位置的顺序红线（确认门/CSAT/补槽守护
   全部优先）由调用侧状态门控保证，见 intent_classify 节点注释。

fail 方向：Redis 故障/解析失败一律返回 None 走正常分类（无害降级）。
"""

import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 接受语词表（与 nba 拒绝语词表同层维护）。命中后做残差判定：
# 去掉接受词剩余字符必须全部是语气/连接成分才算纯接受
_ACCEPT_WORDS = (
    "可以", "好的", "好啊", "好呀", "要的", "需要", "发我看看", "看看",
    "介绍下", "介绍一下", "来一个", "行", "嗯", "要", "ok", "yes", "好",
)
# 语气/连接成分（与 rule_classifier 纯放弃判定同纪律）
_FILLER_CHARS = frozenset("，。！？；、!?,.;: 那就先再都也还是然后吧啊呢哦嗯了的哈呀喔嘛谢您你我们请麻烦帮")
# 纯接受短句长度护栏：超长文本必然带着别的诉求
_MAX_ACCEPT_LEN = 12

# 与 nba.py 保持一致的窗口键模板（唯一读取方；写入在 _record_impression）
_K_LAST = "proactive:last:{tenant}:{session}"


def is_pure_accept(text: str) -> bool:
    """纯接受判定：短句 + 命中接受词 + 残差全是语气成分。

    「好的，另外我要退货」→ 残差含「另外退货」→ 不判（红线 2）；
    「行，麻烦了」→ 残差全是语气词 → 判接受。
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) > _MAX_ACCEPT_LEN:
        return False
    lowered = stripped.lower()
    # 问候语防误伤：「你好/您好」含「好」且残差恰是语气字符，显式排除
    if "你好" in lowered or "您好" in lowered or "hello" in lowered or "hi" in lowered:
        return False
    if not any(w in lowered for w in _ACCEPT_WORDS):
        return False
    residue = lowered
    for word in _ACCEPT_WORDS:
        residue = residue.replace(word, "")
    return all(ch in _FILLER_CHARS for ch in residue)


async def pop_offer_accept(tenant: str, session_id: str, text: str) -> dict[str, Any] | None:
    """接受判定主入口：命中则**消费**窗口键并返回 payload，未命中返回 None。

    消费（DEL）保证同一次展示只能被接受一次——下一轮再说「好的」不会
    重复开任务。payload 无 accept_intent（活动未声明/校验未过）时同样消费
    窗口但返回 None：用户表达了接受而我们无事可办，走正常分类即可。
    """
    if not is_pure_accept(text):
        return None
    try:
        from app.cache.redis_client import get_redis_client

        redis = get_redis_client()
        key = _K_LAST.format(tenant=tenant, session=session_id)
        raw = await redis.get(key)
        if raw is None:
            return None
        await redis.delete(key)
        payload = json.loads(raw)
        accept_intent = str(payload.get("accept_intent") or "")
        if not accept_intent:
            return None
        # 双保险：消费时再校验一次意图已注册（配置热改防御）
        from app.chat.proactive.followups import intent_registered

        if not intent_registered(accept_intent):
            return None
        return {
            "action": str(payload.get("action") or ""),
            "id": str(payload.get("id") or ""),
            "accept_intent": accept_intent,
        }
    except Exception:  # noqa: BLE001 - 判定失败走正常分类（无害降级）
        logger.warning("proactive accept check failed", exc_info=True)
        return None
