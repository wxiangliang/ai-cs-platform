"""对话状态机管理器（DialogStateManager）。

根据「当前状态 + 当前任务 + 挂起任务栈 + 意图 + 槽位」决定状态机如何流转。
核心规则（Stage 03 文档第 8 节 + v2 修订 + Stage 05 扩展）：
1. 退款等写操作但缺 order_id → COLLECTING；
2. active_task 正在等待槽位，用户只发槽位（SLOT_ONLY）→ 续接原任务；
3. “算了/取消” → ABORTED（清空当前任务与挂起栈——用户放弃的是整件事）；
4. “转人工” → HANDOFF（任务与栈一并交给人工）；
5. 写操作槽位齐全也只进 CONFIRMING（确认门），不直接执行；
6. 读操作槽位齐全可 DONE；
7. （v2）CONFIRMING 下确认（META.CONFIRM）→ 任务进入 finished_task 交
   ActionExecutor 执行（Stage 05 起真正执行工具）；否认（META.DENY）→ 取消任务；
8. （Stage 05）补槽/确认门中用户提出**新业务意图** → 旧任务挂起入栈
   （上限 TASK_STACK_MAX，溢出丢最旧）；当前任务结束时从栈恢复（LIFO），
   本轮回复附带续办提示（resumed_task）。

注意：本类是纯决策（不碰数据库、不调 LLM）；返回的 active_task 一律新构造 dict，
禁止原地修改传入对象（JSONB 变更检测约束，见 v2 修订）。
"""

import time
from typing import Any

from app.chat.intent.types import IntentLabel, IntentResult
from app.chat.skills.registry import skill_registry
from app.chat.skills.types import SkillKind
from app.chat.state.types import DialogStateValue, ResolveResult, TurnStatus
from app.core.config import settings


class DialogStateManager:
    """对话状态机。只做状态流转决策，不碰数据库、不调用 LLM。"""

    def resolve(
        self,
        current_state: str,
        active_task: dict[str, Any] | None,
        intent_result: IntentResult,
        slots: dict[str, Any],
        task_stack: list[dict[str, Any]] | None = None,
        pending_intents: list[dict[str, Any]] | None = None,
    ) -> ResolveResult:
        """计算本轮状态流转结果。"""
        intent = intent_result.pred_label
        stack = list(task_stack or [])

        # —— Stage 10 多意图：次要意图构造 pending 任务先压栈（倒序入栈，
        # LIFO 弹出时保持用户表达顺序），主任务结束时自动恢复 ——
        for item in reversed(pending_intents or []):
            p_skill = skill_registry.get(item.get("intent", ""))
            if p_skill.kind not in (SkillKind.READ, SkillKind.WRITE):
                continue
            stack.append(
                {
                    "intent": item.get("intent"),
                    "kind": p_skill.kind,
                    "required_slots": list(p_skill.required_slots),
                    "collected_slots": dict(item.get("slots") or {}),
                    "updated_ts": time.time(),
                }
            )
            while len(stack) > settings.TASK_STACK_MAX:
                stack.pop(0)

        # —— 规则 4：转人工，优先级最高（任务与栈交给人工，不再自动续办）——
        if intent == IntentLabel.META_TRANSFER_HUMAN:
            return ResolveResult(
                new_state=DialogStateValue.HANDOFF,
                status=TurnStatus.HANDOFF,
                active_task=None,
                intent=intent,
                task_stack=[],
            )

        # —— 规则 3：取消，清空当前任务与挂起栈 ——
        if intent == IntentLabel.META_ABORT:
            return ResolveResult(
                new_state=DialogStateValue.ABORTED,
                status=TurnStatus.ABORTED,
                active_task=None,
                intent=intent,
                task_stack=[],
            )

        # 身份询问：直接回复，不影响当前任务
        if intent == IntentLabel.META_BOT_IDENTITY:
            return ResolveResult(
                new_state=current_state or DialogStateValue.IDLE,
                status=TurnStatus.DONE,
                active_task=active_task,
                intent=intent,
                task_stack=stack,
            )

        # —— 规则 7：确认门应答 ——
        if intent == IntentLabel.META_CONFIRM:
            if active_task and current_state == DialogStateValue.CONFIRMING:
                # 确认通过：任务交 ActionExecutor 执行（finished_task），并尝试恢复挂起任务
                return self._finish_turn(
                    status=TurnStatus.CONFIRMED,
                    intent=active_task.get("intent"),
                    collected=dict(active_task.get("collected_slots", {})),
                    stack=stack,
                    finished_task=dict(active_task),
                )
            return self._fallback(current_state, stack)

        if intent == IntentLabel.META_DENY:
            if active_task and current_state == DialogStateValue.CONFIRMING:
                # 否认：取消当前任务，不执行；尝试恢复挂起任务
                return self._finish_turn(
                    status=TurnStatus.ABORTED,
                    intent=intent,
                    collected={},
                    stack=stack,
                    end_state=DialogStateValue.ABORTED,
                )
            return self._fallback(current_state, stack)

        # —— 规则 2：纯槽位输入，续接正在补槽的任务 ——
        if intent == IntentLabel.META_SLOT_ONLY:
            if active_task:
                return self._advance_task(active_task, slots, stack)
            return self._fallback(current_state, stack)

        # 未知意图：若正在补槽，则并入本轮槽位并重新评估；否则兜底
        if intent == IntentLabel.META_UNKNOWN:
            if active_task:
                return self._advance_task(active_task, slots, stack)
            return self._fallback(current_state, stack)

        skill = skill_registry.get(intent)

        # 闲聊 / META 回复类（含投诉）：直接 DONE，不改动当前任务
        if skill.kind in (SkillKind.CHITCHAT, SkillKind.META):
            return ResolveResult(
                new_state=current_state or DialogStateValue.IDLE,
                status=TurnStatus.DONE,
                active_task=active_task,
                intent=intent,
                task_stack=stack,
            )

        # —— 规则 8（Stage 05）：进行中任务被新业务意图打断 → 旧任务挂起入栈 ——
        inherited: dict[str, Any] = {}
        if active_task and current_state in (
            DialogStateValue.COLLECTING,
            DialogStateValue.CONFIRMING,
        ):
            if active_task.get("intent") != intent:
                stack.append(dict(active_task))
                # 栈深上限：溢出丢最旧（FIFO 淘汰）
                while len(stack) > settings.TASK_STACK_MAX:
                    stack.pop(0)
                # 上下文槽位继承（skills 设计原则 4 inherit_from_context）：
                # 「先帮我查下这个订单的物流」——新任务缺的槽位从被挂起任务继承，
                # 本轮显式给出的槽位优先于继承值
                old_slots = active_task.get("collected_slots", {})
                inherited = {
                    k: v
                    for k, v in old_slots.items()
                    if k in skill.required_slots and not slots.get(k)
                }
            # 同意图重复表达：直接用新槽位重建任务（不入栈）

        # —— 业务意图：开启新任务 ——
        task = {
            "intent": intent,
            "kind": skill.kind,
            "required_slots": list(skill.required_slots),
            "collected_slots": {**inherited, **slots},
        }
        return self._evaluate_task(task, stack)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _advance_task(
        self,
        active_task: dict[str, Any],
        slots: dict[str, Any],
        stack: list[dict[str, Any]],
    ) -> ResolveResult:
        """把新抽到的槽位并入当前任务并重新评估（构造新 dict，见模块注释）。"""
        merged = {**active_task.get("collected_slots", {}), **slots}
        new_task = {**active_task, "collected_slots": merged}
        return self._evaluate_task(new_task, stack)

    def _evaluate_task(
        self, task: dict[str, Any], stack: list[dict[str, Any]]
    ) -> ResolveResult:
        """根据任务的必填槽位与读/写性质决定状态。

        每次评估刷新 updated_ts（任务时效依据）；
        追问计数超过 TASK_MAX_ASKS → 放弃任务并建议转人工（Stage 10 流转治理）。
        """
        task = {**task, "updated_ts": time.time()}
        required: list[str] = task.get("required_slots", [])
        collected: dict[str, Any] = task.get("collected_slots", {})
        intent = task.get("intent")
        kind = task.get("kind")

        missing = [s for s in required if not collected.get(s)]
        if missing:
            # 追问上限：连续追问仍拿不到必填槽位 → 放弃，避免流转链无限拉长
            ask_count = int(task.get("ask_count", 0)) + 1
            if ask_count > settings.TASK_MAX_ASKS:
                result = self._finish_turn(
                    status=TurnStatus.FALLBACK,
                    intent=intent,
                    collected=collected,
                    stack=stack,
                    end_state=DialogStateValue.ABORTED,
                )
                result.gave_up = True
                return result
            task["ask_count"] = ask_count
            # 规则 1：缺槽位 → 继续补槽
            return ResolveResult(
                new_state=DialogStateValue.COLLECTING,
                status=TurnStatus.NEEDS_SLOT,
                active_task=task,
                missing_slot=missing[0],
                intent=intent,
                collected_slots=collected,
                task_stack=stack,
            )

        if kind == SkillKind.WRITE:
            # 规则 5：写操作槽位齐全也只进确认门，绝不直接执行
            return ResolveResult(
                new_state=DialogStateValue.CONFIRMING,
                status=TurnStatus.NEEDS_CONFIRM,
                active_task=task,
                intent=intent,
                collected_slots=collected,
                task_stack=stack,
            )

        # 规则 6：读操作槽位齐全可完成，任务结束（尝试恢复挂起任务）
        return self._finish_turn(
            status=TurnStatus.DONE, intent=intent, collected=collected, stack=stack
        )

    def _finish_turn(
        self,
        status: str,
        intent: str | None,
        collected: dict[str, Any],
        stack: list[dict[str, Any]],
        finished_task: dict[str, Any] | None = None,
        end_state: str = DialogStateValue.DONE,
    ) -> ResolveResult:
        """任务结束（DONE/CONFIRMED/DENY）：栈里有挂起任务则恢复（LIFO）。

        恢复的任务重新评估得到其等待状态（COLLECTING/CONFIRMING），
        但本轮 status 仍属于刚结束的动作；回复的续办提示由 save_turn 渲染。
        """
        if stack:
            resumed = dict(stack.pop())
            revived = self._evaluate_task(resumed, stack)
            if revived.status == TurnStatus.DONE:
                # 恢复的读任务槽位已齐（多意图常见）：本轮路由已定，无法当轮执行查询。
                # 保持其为等待中的活动任务并标记 ready，续办提示引导用户回复「继续」，
                # 下一轮任意消息续接任务即触发查询执行（tool_invoke/product 路由）
                ready_task = {**resumed, "ready": True, "updated_ts": time.time()}
                return ResolveResult(
                    new_state=DialogStateValue.COLLECTING,
                    status=status,
                    active_task=ready_task,
                    intent=intent,
                    collected_slots=collected,
                    task_stack=revived.task_stack,
                    finished_task=finished_task,
                    resumed_task=ready_task,
                )
            return ResolveResult(
                new_state=revived.new_state,
                status=status,
                active_task=revived.active_task,
                missing_slot=revived.missing_slot,
                intent=intent,
                collected_slots=collected,
                task_stack=revived.task_stack,
                finished_task=finished_task,
                resumed_task=revived.active_task,
            )
        return ResolveResult(
            new_state=end_state,
            status=status,
            active_task=None,
            intent=intent,
            collected_slots=collected,
            task_stack=stack,
            finished_task=finished_task,
        )

    @staticmethod
    def _fallback(current_state: str, stack: list[dict[str, Any]]) -> ResolveResult:
        """兜底：未知意图，状态保持/回到 IDLE，标记 FALLBACK。"""
        return ResolveResult(
            new_state=current_state or DialogStateValue.IDLE,
            status=TurnStatus.FALLBACK,
            active_task=None,
            intent=IntentLabel.META_UNKNOWN,
            task_stack=stack,
        )


# 模块级单例
dialog_state_manager = DialogStateManager()
