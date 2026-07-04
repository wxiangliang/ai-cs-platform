"""RAG 检索评估门禁（docs/testing/rag_eval_set.md 的执行 harness）。

- 用例表从评估集文档解析（单一事实来源，文档改用例即改门禁）；
- 评估租户 rag-eval 首次自动播种标准语料（幂等）；
- KB 关闭或 Milvus 不可达时显式 skip（不允许静默当作通过）；
- 阈值双档：hash embedding 低档（hit>=0.75 / refusal>=0.80），真实 embedding 高档（0.90/0.90）。
"""

import re
from pathlib import Path

import pytest

from app.core.config import settings
from tests.eval.rag_corpus import DOCUMENTS, EVAL_TENANT, FAQS

EVAL_DOC = Path("docs/testing/rag_eval_set.md")

pytestmark = pytest.mark.skipif(
    not settings.KB_ENABLED, reason="KB_ENABLED=false，RAG 评估门禁跳过（显式）"
)

# 阈值双档
_IS_HASH = settings.EMBEDDING_PROVIDER == "hash"
HIT_GATE = 0.75 if _IS_HASH else 0.90
REFUSAL_GATE = 0.80 if _IS_HASH else 0.90


def _load_cases() -> list[dict[str, str]]:
    """解析评估集文档的用例表（| id | type | query | expect |）。"""
    cases = []
    for line in EVAL_DOC.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*([FDRP]\d+)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line)
        if m:
            cases.append(
                {"id": m.group(1), "type": m.group(2), "query": m.group(3), "expect": m.group(4)}
            )
    assert len(cases) >= 30, f"评估集应 >=30 组，实际解析出 {len(cases)}"
    return cases


async def _seed_if_empty(session) -> None:
    """评估租户无文档时播种标准语料（幂等）。"""
    from sqlalchemy import text

    from app.kb.ingest import kb_ingest_service

    count = (
        await session.execute(
            text("SELECT count(*) FROM kb_document WHERE tenant_id = :t"), {"t": EVAL_TENANT}
        )
    ).scalar_one()
    if count == 0:
        for title, content in DOCUMENTS.items():
            await kb_ingest_service.upsert_document(session, EVAL_TENANT, title, content)
        for question, answer in FAQS.items():
            await kb_ingest_service.upsert_faq(session, EVAL_TENANT, question, answer)
        await session.commit()


async def test_rag_eval_gate(monkeypatch):
    """执行全部用例：hit_rate 与 refusal_acc 双门禁。"""
    from app.db.session import AsyncSessionLocal, dispose_engine
    from app.kb.answerer import rag_answerer

    if _IS_HASH:
        # 开发模式阈值（runbook 口径）：hash 向量分数整体偏低
        monkeypatch.setattr(settings, "FAQ_HIT_THRESHOLD", 0.6)
        monkeypatch.setattr(settings, "RAG_MIN_SCORE", 0.2)

    cases = _load_cases()
    hit_total = hit_ok = refuse_total = refuse_ok = 0
    detail: list[str] = []

    try:
        async with AsyncSessionLocal() as session:
            try:
                await _seed_if_empty(session)
            except Exception as exc:  # noqa: BLE001 - 向量服务不可达 → 显式 skip
                pytest.skip(f"语料播种失败（Milvus 不可达？）：{exc}")

            for case in cases:
                if case["type"] == "route":
                    continue  # 路由红线在 stage06 路由用例断言，不进本 harness
                answer, _trace = await rag_answerer.answer(session, EVAL_TENANT, case["query"])
                if case["type"] == "refuse":
                    refuse_total += 1
                    ok = answer is None
                    refuse_ok += ok
                else:
                    hit_total += 1
                    if case["type"] == "faq":
                        # FAQ 命中或降级到 doc 层答出都算命中
                        ok = answer is not None and (
                            case["expect"] in answer.reply or bool(answer.citations)
                        )
                    else:  # doc：引用含期望文档
                        ok = answer is not None and any(
                            case["expect"] in c for c in answer.citations
                        )
                    hit_ok += ok
                if not ok:
                    got = "REFUSED" if answer is None else f"{answer.source}:{answer.citations}"
                    detail.append(f"  MISS {case['id']} [{case['query']}] -> {got}")
    finally:
        await dispose_engine()

    hit_rate = hit_ok / hit_total if hit_total else 0.0
    refusal_acc = refuse_ok / refuse_total if refuse_total else 0.0
    report = (
        f"hit_rate={hit_rate:.2%} ({hit_ok}/{hit_total}, gate {HIT_GATE:.0%})  "
        f"refusal_acc={refusal_acc:.2%} ({refuse_ok}/{refuse_total}, gate {REFUSAL_GATE:.0%})"
    )
    print("\nRAG eval:", report)
    if detail:
        print("\n".join(detail))
    assert hit_rate >= HIT_GATE, f"检索命中率未达门禁：{report}"
    assert refusal_acc >= REFUSAL_GATE, f"拒答准确率未达门禁：{report}"
