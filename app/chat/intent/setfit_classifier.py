"""SetFit 语义意图分类器推理封装。

- 模型进程内单例、懒加载（首次调用时加载，加载失败置为不可用并告警）；
- 推理是 CPU 密集的同步计算，调用方（HybridIntentClassifier）需用
  asyncio.to_thread 包装，避免阻塞事件循环；
- 只输出 (label, confidence, top_k)，不做阈值判断——阈值决策在混合分类器层。

模型产物由 scripts/train_setfit_intent.py 生成（models/intent_setfit_v1/）。
"""

import threading
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SetFitIntentModel:
    """SetFit 模型推理器（线程安全懒加载单例）。"""

    def __init__(self, model_path: str | None = None) -> None:
        self._model_path = model_path or settings.SETFIT_MODEL_PATH
        self._model: Any = None
        self._unavailable = False  # 加载失败后置 True，不再反复重试
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """模型是否可用（触发一次加载）。"""
        self._ensure_loaded()
        return self._model is not None

    def _ensure_loaded(self) -> None:
        """懒加载模型：目录不存在或加载异常都视为不可用（由上层降级规则分类）。"""
        if self._model is not None or self._unavailable:
            return
        with self._lock:
            if self._model is not None or self._unavailable:
                return
            path = Path(self._model_path)
            # 相对路径锚定仓库根（Stage 13）：任意 CWD 启动都能找到模型
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[3] / path
            if not path.exists():
                logger.warning("setfit model path not found: %s, classifier degraded", path)
                self._unavailable = True
                return
            try:
                from setfit import SetFitModel

                self._model = SetFitModel.from_pretrained(str(path))
                logger.info("setfit intent model loaded: %s", path)
            except Exception:  # noqa: BLE001 - 加载失败降级规则分类，不抛出
                logger.exception("failed to load setfit model: %s", path)
                self._unavailable = True

    def encode(self, text: str) -> Any:
        """返回归一化句向量（shape (dim,)）。示例向量交叉验证复用同一语义体，
        保证「分类的表示」与「近邻的表示」同源。调用前必须确认 available。"""
        model = self._model
        assert model is not None, "setfit model not loaded"
        return model.model_body.encode([text], normalize_embeddings=True)[0]

    def predict(self, text: str, top_k: int = 3) -> tuple[str, float, list[dict[str, Any]]]:
        """同步推理：返回 (预测标签, 置信度, top_k 列表)。

        调用前必须确认 available；本方法为 CPU 密集同步调用。
        """
        model = self._model
        assert model is not None, "setfit model not loaded"
        # normalize 后的向量 + 分类头概率
        emb = model.model_body.encode([text], normalize_embeddings=True)[0]
        return self.predict_from_embedding(emb, top_k=top_k)

    def predict_from_embedding(
        self, embedding: Any, top_k: int = 3
    ) -> tuple[str, float, list[dict[str, Any]]]:
        """用已有句向量做分类头推理（Stage 30 一轮只编码一次：
        Mode Gate 与意图头共享同一 embedding，省掉重复的 body 前向）。"""
        model = self._model
        assert model is not None, "setfit model not loaded"
        probs = model.model_head.predict_proba([embedding])[0]
        classes = list(model.model_head.classes_)
        ranked = sorted(zip(classes, probs), key=lambda x: x[1], reverse=True)[:top_k]
        top = [{"label": label, "score": round(float(p), 4)} for label, p in ranked]
        return top[0]["label"], top[0]["score"], top


# 模块级单例
setfit_intent_model = SetFitIntentModel()
