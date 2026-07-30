"""示例向量 TopK 交叉验证（Stage 26 遗留 3 落地；**默认关闭**，启用时机见
docs/intent/README.md「示例向量交叉验证与 LTR 路线」）。

解决什么：SetFit 分类头在 top1/top2 分差小（margin 小）时不可靠，现行做法
是交 LLM 二判（有成本/延迟）。本模块提供第二个**独立**信号：查询与训练集
示例的最近邻——「这句话和哪个意图的真实样本最像」。KNN 同意 top1 时免二判
直接采纳（省成本），不同意时证据附加、仍走二判（**绝不改选**，与 margin
纪律一致）。

它同时是未来 LTR 重排器最重要的特征之一（stage-27 评审：LTR 需真实标注
数据成熟后再上，本模块是其免训练前置）。

实现约束：
- 向量用 SetFit 自己的语义体编码（分类与近邻同源表示，不引第二个模型）；
- 索引是离线产物（scripts/build_intent_example_index.py 生成，不进 git），
  缺失/加载失败 → 静默停用（与 meta_shadow 同降级纪律）；
- numpy 余弦（行向量已归一化，点积即余弦），单查 ~1ms 级。
"""

import json
import threading
from pathlib import Path
from typing import Any

from app.chat.intent.setfit_classifier import setfit_intent_model
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ExampleKnnIndex:
    """训练集示例向量索引（线程安全懒加载单例）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self._matrix: Any = None  # np.ndarray (n, dim)，行已 L2 归一化
        self._labels: list[str] = []

    def _load(self) -> None:
        base = Path(settings.INTENT_EXAMPLE_INDEX_DIR)
        if not base.is_absolute():
            # 相对路径锚定仓库根（Stage 13 纪律）
            base = Path(__file__).resolve().parents[3] / base
        try:
            emb_path = base / "embeddings.npy"
            label_path = base / "labels.json"
            if not (emb_path.exists() and label_path.exists()):
                logger.info("example knn index not found: %s, disabled", base)
                return
            import numpy as np

            self._matrix = np.load(emb_path)
            self._labels = json.loads(label_path.read_text(encoding="utf-8"))
            if len(self._labels) != self._matrix.shape[0]:
                logger.warning("example knn index corrupt (labels != rows), disabled")
                self._matrix = None
                self._labels = []
                return
            logger.info(
                "example knn index loaded: %d examples, dim=%d",
                self._matrix.shape[0], self._matrix.shape[1],
            )
        except Exception:  # noqa: BLE001 - 索引加载失败只停用，不打断主链路
            logger.exception("example knn index load failed, disabled")
            self._matrix = None
            self._labels = []

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            with self._lock:
                if not self._loaded:
                    self._load()
                    self._loaded = True

    def query(self, text: str) -> dict[str, Any] | None:
        """KNN 查询：返回 {label, similarity, votes, top}；不可用返回 None。

        label = topk 近邻中的多数标签；similarity = 该标签近邻的平均余弦；
        votes = 多数标签票数（/topk）。CPU 同步计算，调用方用 to_thread 包装。
        """
        self._ensure_loaded()
        if self._matrix is None or not setfit_intent_model.available:
            return None
        try:
            import numpy as np

            emb = setfit_intent_model.encode(text)
            sims = self._matrix @ np.asarray(emb, dtype=self._matrix.dtype)
            k = min(settings.INTENT_EXAMPLE_KNN_TOPK, len(self._labels))
            top_idx = np.argpartition(-sims, k - 1)[:k]
            top_idx = top_idx[np.argsort(-sims[top_idx])]
            top = [(self._labels[i], float(sims[i])) for i in top_idx]
            # 多数标签 + 其平均相似度
            counts: dict[str, list[float]] = {}
            for label, sim in top:
                counts.setdefault(label, []).append(sim)
            best_label, best_sims = max(
                counts.items(), key=lambda kv: (len(kv[1]), sum(kv[1]) / len(kv[1]))
            )
            return {
                "label": best_label,
                "similarity": round(sum(best_sims) / len(best_sims), 4),
                "votes": f"{len(best_sims)}/{k}",
                "top": [{"label": lb, "sim": round(s, 4)} for lb, s in top],
            }
        except Exception:  # noqa: BLE001 - 查询失败视为无信号
            logger.warning("example knn query failed", exc_info=True)
            return None


# 模块级单例
example_knn_index = ExampleKnnIndex()


def reset_for_test() -> None:
    """测试专用：重置懒加载状态。"""
    global example_knn_index
    example_knn_index = ExampleKnnIndex()
