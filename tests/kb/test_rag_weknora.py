"""RAG 强化批（WeKnora 对齐）回归：查询改写 / 引用溯源 / 段落清洗 / 缓存绕过。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.config import settings
from app.kb.answerer import RagAnswer, _build_context, _parse_cited
from app.kb.query_rewrite import is_vague_query, rewrite_vague_query
from app.kb.retriever import RetrievalTrace
from app.kb.types import Hit

# ---------------- 查询改写 ----------------


def test_is_vague_query_heuristics():
    assert is_vague_query("刚才那个多少钱")  # 指代词
    assert is_vague_query("还有别的颜色吗")
    assert is_vague_query("多少钱")  # 过短且无实体
    assert not is_vague_query("退货运费谁承担")  # 清晰独立
    assert not is_vague_query("AP-300")  # 短但含型号实体
    assert not is_vague_query("")


async def test_rewrite_uses_llm_and_governs_output(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake")
    mock = AsyncMock(return_value="AP-300 空气净化器价格\n多余的解释行")
    monkeypatch.setattr("app.kb.query_rewrite.chat_completion", mock)
    memory = {"recent_turns": [["user", "AP-300 有货吗"], ["assistant", "有货"]]}

    # 含糊查询 → 改写并只取首行
    assert await rewrite_vague_query("那个多少钱", memory) == "AP-300 空气净化器价格"
    # 清晰查询 → 不调用 LLM
    mock.reset_mock()
    assert await rewrite_vague_query("退货运费谁承担", memory) is None
    mock.assert_not_awaited()
    # 无近期对话 → 不改写（缺乏改写依据）
    assert await rewrite_vague_query("那个多少钱", {"recent_turns": []}) is None
    # 输出与原文相同 → 视为无需改写
    mock2 = AsyncMock(return_value="那个多少钱")
    monkeypatch.setattr("app.kb.query_rewrite.chat_completion", mock2)
    assert await rewrite_vague_query("那个多少钱", memory) is None


async def test_rewrite_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    memory = {"recent_turns": [["user", "AP-300 有货吗"]]}
    assert await rewrite_vague_query("那个多少钱", memory) is None


# ---------------- 引用溯源 ----------------


def _hits(n: int) -> list[Hit]:
    return [
        Hit(id=f"c{i}", score=0.9, source_backend="m", title=f"文档{i}", content=f"内容{i}")
        for i in range(1, n + 1)
    ]


def test_parse_cited_maps_indices_to_hits():
    hits = _hits(3)
    cited = _parse_cited("答案要点 [1]。补充说明 [3]。（来源：《文档1》）", hits)
    assert [h.id for h in cited] == ["c1", "c3"]


def test_parse_cited_fallback_when_no_marks():
    hits = _hits(2)
    # 无编号 / 编号越界 → 回退全部命中（citations 宁多勿漏）
    assert _parse_cited("没有引用标记的回答", hits) == hits
    assert _parse_cited("越界引用 [9]", hits) == hits


# ---------------- 段落清洗 ----------------


def test_build_context_dedupes_across_hits():
    shared = "第一条 运费规则\n第二条 质量问题免运费"
    h1 = Hit(id="a", score=0.9, source_backend="m", title="退货政策", content="x",
             extra={"section_context": shared})
    h2 = Hit(id="b", score=0.8, source_backend="m", title="退货政策", content="y",
             extra={"section_context": shared + "\n第三条 补充条款"})
    ctx = _build_context([h1, h2])
    # 编号与 hits 下标严格对应（引用溯源依赖）
    assert "[1]《退货政策》" in ctx and "[2]《退货政策》" in ctx
    # 重复段落只出现一次；第二块只剩差异行
    assert ctx.count("第一条 运费规则") == 1
    assert ctx.count("第二条 质量问题免运费") == 1
    assert "第三条 补充条款" in ctx
    # 整块重复 → 占位不丢编号
    ctx2 = _build_context([h1, Hit(id="c", score=0.7, source_backend="m", title="退货政策",
                                   content="x", extra={"section_context": shared})])
    assert "（内容与前文资料重复，略）" in ctx2


# ---------------- 改写轮次绕过语义缓存（节点级） ----------------


async def test_rewritten_turn_bypasses_semantic_cache(monkeypatch):
    from app.chat.graph.nodes import rag_answer as node_mod

    calls = {"lookup": 0, "store": 0}

    class _SpyCache:
        async def lookup(self, *args, **kwargs):
            calls["lookup"] += 1
            return None

        async def store(self, *args, **kwargs):
            calls["store"] += 1

    monkeypatch.setattr(node_mod, "get_semantic_cache", lambda: _SpyCache())
    monkeypatch.setattr(
        "app.kb.query_rewrite.rewrite_vague_query",
        AsyncMock(return_value="AP-300 空气净化器价格"),
    )
    trace = RetrievalTrace(query="q", backend="fake")
    answer = RagAnswer(reply="799 元 [1]", source="rag_llm", citations=["《价格表》"], trace=trace)
    answer_mock = AsyncMock(return_value=(answer, trace))
    monkeypatch.setattr(node_mod, "rag_answerer", SimpleNamespace(answer=answer_mock))

    state = {
        "tenant_id": "t1",
        "session_id": "s1",
        "normalized_text": "那个多少钱",
        "intent_result": {"pred_label": "FAQ.GENERAL"},
        "memory": {"recent_turns": [["user", "AP-300 有货吗"]]},
    }
    result = await node_mod.rag_answer(state, {"configurable": {"db_session": None}})

    assert result["reply"] == "799 元 [1]"
    assert calls == {"lookup": 0, "store": 0}  # 改写轮次完全绕过共享缓存
    # 检索用的是改写后的查询，且原话留痕
    assert answer_mock.await_args.args[2] == "AP-300 空气净化器价格"
    assert result["retrieval"]["rewritten_from"] == "那个多少钱"


# ---------------- 关键词召回细排（BM25-lite） ----------------


def test_keyword_rank_count_first_then_weighted():
    from app.repositories.kb_chunk_repository import rank_keyword_matches

    def _chunk_ns(cid, content):
        return SimpleNamespace(id=cid, content=content)

    keywords = ["退货", "AP-300"]
    # a：命中 2 词、内容短（最聚焦）；b：命中 2 词、内容长；c：只命中 1 词
    a = _chunk_ns("a", "AP-300 退货规则")
    b = _chunk_ns("b", "AP-300 退货规则。" + "无关内容填充。" * 50)
    c = _chunk_ns("c", "退货说明")
    ranked = rank_keyword_matches([c, b, a], keywords, limit=10)
    assert [x[0].id for x in ranked] == ["a", "b", "c"]  # 命中数优先，同数短块在前
    assert [x[1] for x in ranked] == [2, 2, 1]  # 返回值仍是 (chunk, 命中词数)
