"""知识库（RAG / FAQ）包。

Stage 06：PostgreSQL 为唯一事实来源（原文/分块/向量都入 PG），
向量检索后端（当前 Milvus）只是可随时重建的索引视图。
上层只依赖 backends.base 的抽象接口，不感知具体后端。
"""
