"""工具层（Stage 05）：ToolProvider 协议 + mock 实现。

红线：写操作工具（create_*/cancel_*/update_*）只能由 ActionExecutor 调用，
读操作工具由 tool_invoke / product_answer 等查询节点调用。
"""
