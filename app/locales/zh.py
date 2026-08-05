"""中文语言包（Stage 19，源语言）。

值逐字对齐迁移前的硬编码字符串——i18n 收口是纯重构，默认语言中文输出必须不变。
占位符沿用 `{name}` 风格，由 i18n.t(**params) 安全填充（缺键返回空串）。
"""

TABLE: dict[str, str] = {
    # —— 模板回复兜底（responder）——
    "responder.safe_fallback": "我需要再确认一下，您是想咨询商品、订单、物流还是售后问题？",
    "responder.failed": "抱歉，我暂时无法处理这条消息，请换个说法或稍后再试。",
    # —— 内容安全护栏（Stage 14）——
    "guardrail.injection": "抱歉，这个请求我无法处理。如果您有商品、订单、物流或售后方面的问题，我很乐意为您解答。",
    "guardrail.abuse_severe": "请文明沟通，我会尽力帮您解决问题。如果您对服务有不满，可以回复「转人工」由人工客服跟进。",
    "guardrail.repeat_flood": "您已多次发送相同内容。请换一种说法描述您的问题，或回复「转人工」由人工客服协助。",
    "guardrail.abuse_handoff": "已为您转接人工客服跟进，请稍候。也请您文明沟通，谢谢配合。",
    # —— 人工接管 / CSAT（Stage 07/15）——
    "handoff.silent_waiting": "人工客服正在为您服务，您的消息已转达，请稍候。",
    "handoff.ticket_suffix": "（工单号：{ticket}{queue}）",
    "handoff.queue_note": "，当前排队第 {position} 位",
    "handoff.repeated_unknown": "\n看起来我可能没帮上您——已为您登记人工客服跟进，您也可以直接回复「转人工」。",
    "csat.ask_handoff": "本次人工服务已结束。麻烦您为本次服务打个分（1-5 分，5 分最满意），也可以直接回复「满意」或「不满意」。",
    "csat.ask_session_close": "本次会话已结束。麻烦您为本次服务打个分（1-5 分，5 分最满意），也可以直接回复「满意」或「不满意」。",
    "csat.thanks_low": "感谢您的反馈，很抱歉这次服务没让您满意，我们会认真改进。您随时可以继续咨询或回复「转人工」。",
    "csat.thanks_high": "感谢您的评价！有任何问题随时找我。",
    # —— 确认门 / 任务治理（Stage 13/10）——
    "confirm.weak_recheck_prefix": "为避免误操作，请您回复「确认」以继续执行。{draft}",
    "task.gave_up": "多次沟通仍未获取到所需信息，本次操作先为您取消了；如需继续办理，可以再次说明诉求，或回复「转人工」由人工客服协助您。",
    # —— 任务续办提示（Stage 05/10）——
    "resume.ready": "信息已齐，回复「继续」即可为您查询。",
    "resume.suspended": "\n另外，我们继续您刚才的「{name}」：{ask}",
    "resume.pending": "\n另外，关于您提到的「{name}」：{ask}",
    # —— Stage 23 方向纠偏 ——
    "resume.suspended_optional": "\n另外，刚才的「{name}」还没有开始办理。如需继续：{ask}如果不需要，回复「不是要办这个」即可。",
    "task.denied_redirect": "好的，先不办理「{name}」了。请问您想咨询或办理什么？直接告诉我就可以。",
    "intent.soft_confirm": "您是想「{name}」对吗？如果不是，直接说您的需求即可。",
    # —— Stage 26 意图决策加固 ——
    "intent.switch_clarify_collecting": "我这边还在处理「{name}」：{question}如果您想先办理「{new_name}」，请再明确说一下，我会先挂起当前业务。",
    "intent.switch_clarify_confirming": "当前「{name}」正在等待您的确认：{question}如需办理「{new_name}」，请先回复「确认」或「不用」结束当前步骤。",
    "intent.unknown_with_task": "抱歉，我没太理解这句话。如果是继续办理「{name}」，{question}如果想咨询或办理其他业务，直接说明您的诉求即可。",
    # —— Stage 31 主动服务 ——
    "proactive.campaign_mention": "\n对了，{hook}。如不感兴趣可以忽略，回复「不用推荐」以后就不再提啦。",
    # —— Stage 30 对话模式门 ——
    "mode.social_resume": "\n刚才的「{name}」还在办理中，继续提供所需信息就可以，随时可以继续～",
    "mode.oos_boundary": "这个请求超出了我能协助的客服业务范围哈。我可以帮您查订单、物流，办理退换货、退款等售后，或解答商品和平台政策问题～",
    # —— 写操作回执（Stage 05）——
    "action.already_executed": "这笔申请此前已提交成功，无需重复提交。如需查询进度请告诉我工单号。",
    "action.failed": "抱歉，提交时遇到问题，我已记录您的申请，稍后由人工客服跟进处理，请放心。",
    "action.ticket_no": "工单号：{ticket_no}。",
    # —— 商品库（Stage 06-03）——
    "product.multi": "为您找到多款相关商品：{names}。请问您想咨询哪一款？",
    "product.not_found": "抱歉，没有找到与「{query}」匹配的商品，请确认商品名称，或发我商品链接。",
}
