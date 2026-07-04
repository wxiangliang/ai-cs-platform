"""护栏引擎（Stage 14）：输入三层检查 + 输出检查 + 会话级连击/灌注计数。

原则：
- 规则层纯内存正则/词表匹配，零延迟；
- 连击（辱骂）与重复灌注计数走 Redis（短 TTL 会话级，拦截轮不触碰
  dialog_state 的既有语义得以保持）；Redis 故障一律放行（fail-open）；
- 引擎自身任何异常都不允许打断主链路。
"""

import hashlib
from dataclasses import dataclass, field

from app.chat.guardrail.lexicon import GuardrailRule, load_rules
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 会话级计数的 TTL（秒）：辱骂连击/重复灌注都是短时行为
_COUNTER_TTL = 600
# 重复灌注最小文本长度：短于此的（确认词/评分/短应答）不计入，避免误拦
_REPEAT_MIN_CHARS = 5


@dataclass
class InputVerdict:
    """输入检查结论。"""

    action: str = "pass"  # pass / block
    rule_id: str | None = None
    category: str | None = None
    flags: list[str] = field(default_factory=list)  # 不拦截的标记（emotion_negative 等）


class GuardrailEngine:
    """规则护栏引擎（进程内单例，规则启动加载一次）。"""

    def __init__(self) -> None:
        self._rules: list[GuardrailRule] | None = None

    @property
    def rules(self) -> list[GuardrailRule]:
        if self._rules is None:
            try:
                self._rules = load_rules()
            except Exception:  # noqa: BLE001 - 规则库损坏 → 降级放行
                logger.exception("guardrail rules load failed, degraded to pass")
                self._rules = []
        return self._rules

    # ------------------------------------------------------------------
    # 输入护栏
    # ------------------------------------------------------------------
    def check_input(self, text: str) -> InputVerdict:
        """输入三层：注入模式 → 重度违禁 → 轻度情绪标记。"""
        if not settings.GUARDRAIL_ENABLED or not text:
            return InputVerdict()
        verdict = InputVerdict()
        try:
            for rule in self.rules:
                if rule.category == "output_leak" or not rule.match(text):
                    continue
                if rule.action == "block":
                    return InputVerdict(action="block", rule_id=rule.rule_id,
                                        category=rule.category)
                verdict.flags.append(rule.category)
        except Exception:  # noqa: BLE001 - 引擎异常放行
            logger.exception("guardrail input check failed, allowing")
        return verdict

    def check_output(self, text: str) -> str | None:
        """输出护栏：命中泄漏特征/重度违禁返回 rule_id（调用方回退底稿），否则 None。"""
        if not settings.GUARDRAIL_ENABLED or not text:
            return None
        try:
            for rule in self.rules:
                if rule.category in ("output_leak", "abuse_severe") and rule.match(text):
                    return rule.rule_id
        except Exception:  # noqa: BLE001
            logger.exception("guardrail output check failed, allowing")
        return None

    # ------------------------------------------------------------------
    # 会话级计数（Redis，fail-open）
    # ------------------------------------------------------------------
    @staticmethod
    async def bump_abuse_streak(tenant_id: str, session_id: str) -> int:
        """重度违禁连击 +1，返回当前连击数；Redis 故障返回 1（不触发转人工）。"""
        try:
            from app.cache.redis_client import get_redis_client

            key = f"guard:abuse:{tenant_id}:{session_id}"
            redis = get_redis_client()
            count = await redis.incr(key)
            await redis.expire(key, _COUNTER_TTL)
            return int(count)
        except Exception:  # noqa: BLE001
            logger.exception("abuse streak bump failed (redis down?)")
            return 1

    @staticmethod
    async def reset_abuse_streak(tenant_id: str, session_id: str) -> None:
        """正常消息重置连击。"""
        try:
            from app.cache.redis_client import get_redis_client

            await get_redis_client().delete(f"guard:abuse:{tenant_id}:{session_id}")
        except Exception:  # noqa: BLE001
            logger.exception("abuse streak reset failed")

    @staticmethod
    async def check_repeat_flood(tenant_id: str, session_id: str, text: str) -> bool:
        """重复灌注：同会话同文本连发达到上限返回 True（防刷 LLM 成本）。

        短消息跳过：补槽/确认场景用户会对不同问题连答「是/对/好的/5」等短词，
        这些不是灌注（LLM 成本也低）；只对达到最小长度的文本计数，避免误拦应答。
        """
        if len(text.strip()) < _REPEAT_MIN_CHARS:
            return False
        try:
            from app.cache.redis_client import get_redis_client

            digest = hashlib.sha256(text.encode()).hexdigest()[:16]
            key = f"guard:repeat:{tenant_id}:{session_id}"
            redis = get_redis_client()
            prev = await redis.get(key)
            if prev:
                prev_digest, _, prev_count = str(prev).partition(":")
                count = int(prev_count) + 1 if prev_digest == digest else 1
            else:
                count = 1
            await redis.set(key, f"{digest}:{count}", ex=_COUNTER_TTL)
            return count >= settings.GUARDRAIL_REPEAT_LIMIT
        except Exception:  # noqa: BLE001
            logger.exception("repeat flood check failed (redis down?), allowing")
            return False


# 模块级单例
guardrail_engine = GuardrailEngine()
