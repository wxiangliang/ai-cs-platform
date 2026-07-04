"""聊天主链路包（预留）。

第一阶段仅预留目录与包结构，后续阶段将实现：
- graph/   ：LangGraph 流程编排与节点
- intent/  ：意图识别
- slots/   ：槽位抽取
- state/   ：DialogState 状态机
- skills/  ：能力声明 Registry
- llm/     ：LangChain / LLM 调用
- logging/ ：决策日志 DecisionLogger

详见 docs/requirements/chat-framework/fastapi_langgraph_chat_framework.md。
"""
