# -*- coding: utf-8 -*-
"""本地嵌入模型服务（sentence-transformers）。

模型只在首次使用时加载；加载失败会返回稳定错误码，不会静默改用随机向量。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import List, Sequence

from config import EMBEDDING_MODEL, HF_CACHE_DIR, HF_ENDPOINT
from errors import ApiError


log = logging.getLogger("aps-workbench.embedding")

_lock = threading.RLock()
_model = None


def _get_model():
    """延迟加载并缓存 SentenceTransformer 模型。"""
    global _model
    with _lock:
        if _model is not None:
            return _model
        try:
            # sentence-transformers 会读取 HF_* 环境变量；必须在导入前设置。
            HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("HF_ENDPOINT", HF_ENDPOINT)
            os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover
            raise ApiError(
                "EMBEDDING_FAILED",
                "嵌入依赖未安装，请运行 pip install -r requirements.txt。",
                502,
            ) from exc

        try:
            log.info("加载嵌入模型：%s", EMBEDDING_MODEL)
            _model = SentenceTransformer(
                EMBEDDING_MODEL,
                device=os.getenv("EMBEDDING_DEVICE", "cpu"),
            )
            log.info("嵌入模型加载完成")
            return _model
        except Exception as exc:  # noqa: BLE001
            log.exception("嵌入模型加载失败")
            raise ApiError(
                "EMBEDDING_FAILED",
                "嵌入模型加载失败，请检查模型是否已下载或网络是否可用。"
                f"（{type(exc).__name__}）",
                502,
            ) from exc


def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """把一批文本转为归一化向量。

    输入：文本列表
    输出：与文本顺序一致的向量列表
    """
    values = [str(text or "").strip() for text in texts]
    if not values:
        return []
    model = _get_model()
    try:
        vectors = model.encode(
            values,
            batch_size=16,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [[float(value) for value in vector] for vector in vectors]
    except Exception as exc:  # noqa: BLE001
        log.exception("文本向量化失败")
        raise ApiError(
            "EMBEDDING_FAILED",
            f"文本向量化失败：{type(exc).__name__}。",
            502,
        ) from exc


def embed_query(text: str) -> List[float]:
    """把查询文本转为向量。"""
    vectors = embed_texts([text])
    return vectors[0] if vectors else []


def model_name() -> str:
    return EMBEDDING_MODEL
