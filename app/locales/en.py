"""English locale (Stage 19, skeleton demo).

Only a few keys are filled to prove the mechanism; missing keys fall back to
the default locale (zh) via i18n.t(). Full translation is out of scope for the
i18n-foundation stage — add keys here (machine translate + human review) when a
language actually ships. LLM-generated replies do NOT need entries here: the
model answers in the user's language via the prompt instruction.
"""

TABLE: dict[str, str] = {
    # 固定话术示范（其余 key 自动回退中文）
    "responder.safe_fallback": "Could you clarify — is your question about a product, order, shipping, or after-sales?",
    "responder.failed": "Sorry, I can't handle this message right now. Please rephrase or try again later.",
    "guardrail.injection": "Sorry, I can't process that request. I'm happy to help with product, order, shipping, or after-sales questions.",
    "handoff.silent_waiting": "A human agent is assisting you. Your message has been forwarded — please hold on.",
    "csat.thanks_high": "Thanks for your rating! Reach out anytime.",
    "csat.ask_handoff": "Your session with our agent has ended. Please rate this service (1-5, 5 is best), or reply \"satisfied\" / \"unsatisfied\".",
    # skill 模板覆盖示范：非默认语言查 skill.<skill_id>.<template_key>
    "skill.aftersale_refund.collect": "Sure, I'll help with the refund. Please share your order number or the phone number used to order.",
    # Stage 35 identity assurance
    "identity.verify_required": "For your account's security, this operation requires identity verification first. I've connected you to a human agent to verify — once done, we can continue.",
    # Stage 31 proactive service
    "proactive.campaign_mention": "\nBy the way, {hook}. Feel free to ignore this, or reply \"no recommendations\" to opt out.",
    "proactive.onboarding_mention": "\nBy the way, you haven't registered as a member yet — reply \"register\" and I'll set it up in a minute. Feel free to ignore this, or reply \"no recommendations\" to opt out.",
    # Stage 30 conversation mode gate
    "mode.social_resume": "\nYour \"{name}\" request is still in progress — just send the required info to continue.",
    "mode.oos_boundary": "That's outside what I can help with as customer service. I can check orders and shipping, handle returns/refunds, or answer product and policy questions.",
}
