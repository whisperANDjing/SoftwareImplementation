# -*- coding: utf-8 -*-
"""外部 Embedding API 服务。

默认使用 OpenAI-compatible 的 SiliconFlow 接口；不加载本地模型。
未配置 Embedding Key 时返回未配置状态，检索服务降级为内存关键词检索。
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Sequence

from config import (
    EMBEDDING_API_BASE,
    EMBEDDING_API_KEY,
    EMBEDDING_MODEL,
    EMBEDDING_TIMEOUT,
)
from errors import ApiError


log = logging.getLogger("aps-workbench.embedding")

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


def is_configured() -> bool:
    return bool(EMBEDDING_API_KEY and EMBEDDING_API_BASE and OpenAI is not None)


def model_name() -> str:
    return EMBEDDING_MODEL if is_configured() else "lexical-fallback"


def mode() -> str:
    return "external" if is_configured() else "lexical-fallback"


def _client():
    if not is_configured():
        raise ApiError(
            "EMBEDDING_NOT_CONFIGURED",
            "未配置外部 Embedding API，已使用内存关键词检索。",
            502,
        )
    return OpenAI(
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_API_BASE,
        timeout=EMBEDDING_TIMEOUT,
    )


def _normalise(vector: Sequence[float]) -> List[float]:
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        return values
    return [value / norm for value in values]


def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """调用外部 API 批量向量化，返回归一化向量。"""
    values = [str(text or "").strip() for text in texts]
    if not values:
        return []
    try:
        response = _client().embeddings.create(
            model=EMBEDDING_MODEL,
            input=values,
            timeout=EMBEDDING_TIMEOUT,
        )
        items = sorted(
            response.data,
            key=lambda item: int(getattr(item, "index", 0)),
        )
        vectors = [_normalise(item.embedding) for item in items]
        if len(vectors) != len(values):
            raise ValueError("Embedding 返回数量与输入不一致")
        return vectors
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("外部 Embedding 调用失败")
        raise ApiError(
            "EMBEDDING_FAILED",
            f"Embedding API 调用失败：{type(exc).__name__}。请检查 Key、模型名和网络。",
            502,
        ) from exc


def embed_query(text: str) -> List[float]:
    vectors = embed_texts([text])
    return vectors[0] if vectors else []


def try_embed_query(text: str) -> Optional[List[float]]:
    """有配置则尝试外部向量；无配置或 API 失败时返回 None，由检索降级。"""
    if not is_configured():
        return None
    try:
        return embed_query(text)
    except ApiError:
        log.warning("查询 Embedding 失败，降级为内存关键词检索", exc_info=True)
        return None
