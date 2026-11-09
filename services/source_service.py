# -*- coding: utf-8 -*-
"""RAG 来源详情：按 chunk_id 读取原文、相邻片段和定位信息。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from data import context_store, vector_store
from errors import ApiError


def _location(metadata: Dict[str, Any]) -> Dict[str, Any]:
    location = {
        "file_name": metadata.get("file", "未知文件"),
        "sheet": metadata.get("sheet"),
        "page": metadata.get("page"),
        "row": metadata.get("row_start"),
        "row_end": metadata.get("row_end"),
        "paragraph": metadata.get("paragraph_start"),
        "paragraph_end": metadata.get("paragraph_end"),
        "chunk_index": metadata.get("chunk_index"),
    }
    return {key: value for key, value in location.items() if value is not None}


def _chunk_payload(chunk: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not chunk:
        return None
    metadata = chunk.get("metadata") or {}
    payload = {
        "chunk_id": chunk["chunk_id"],
        "text": chunk.get("text", ""),
    }
    payload.update(_location(metadata))
    return payload


def get_source(project_id: str, chunk_id: str) -> Dict[str, Any]:
    """读取单个切片及前后各一个切片；禁止跨项目读取。"""
    if not context_store.get_project(project_id):
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    if not chunk_id.strip():
        raise ApiError("VALIDATION_ERROR", "缺少 chunk_id。", 400)
    try:
        context = vector_store.get_source_context(project_id, chunk_id)
    except vector_store.VectorStoreError as exc:
        raise ApiError("VECTOR_STORE_FAILED", str(exc), 500) from exc
    if not context:
        raise ApiError(
            "SOURCE_NOT_FOUND",
            "来源片段不存在，或不属于当前项目。",
            404,
        )
    current = context["current"]
    metadata = current.get("metadata") or {}
    location = _location(metadata)
    return {
        "file_name": location.get("file_name", "未知文件"),
        "chunk_id": current["chunk_id"],
        "snippet": str(current.get("text", ""))[:500],
        "text": current.get("text", ""),
        **location,
        "before": _chunk_payload(context.get("before")),
        "after": _chunk_payload(context.get("after")),
    }
