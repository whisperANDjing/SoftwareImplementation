# -*- coding: utf-8 -*-
"""内存文档切片索引。

不使用 Chroma 或本地向量数据库。切片和可选的外部 Embedding 向量常驻内存，
并快照到 storage/vector_index.json，进程重启后可恢复；检索本身完全在内存中完成。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from typing import Any, Dict, List, Optional, Sequence

from config import VECTOR_INDEX_FILE


log = logging.getLogger("aps-workbench.vector-store")

_lock = threading.RLock()
_chunks: Dict[str, Dict[str, Any]] = {}
_LEXICAL_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]{3,}")
_STOP_CHARS = set("的了吗呢请问帮我看看这个那个以及和与及里面")


class VectorStoreError(RuntimeError):
    """内存索引读写失败。"""


def _load() -> Dict[str, Dict[str, Any]]:
    if not VECTOR_INDEX_FILE.exists():
        return {}
    try:
        data = json.loads(VECTOR_INDEX_FILE.read_text(encoding="utf-8"))
        chunks = data.get("chunks", {}) if isinstance(data, dict) else {}
        if not isinstance(chunks, dict):
            return {}
        return {
            str(chunk_id): record
            for chunk_id, record in chunks.items()
            if isinstance(record, dict)
        }
    except Exception:  # noqa: BLE001
        log.exception("内存索引快照读取失败，将从空索引启动")
        return {}


_chunks = _load()


def _save() -> None:
    VECTOR_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = VECTOR_INDEX_FILE.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump({"chunks": _chunks}, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, VECTOR_INDEX_FILE)


def add_chunks(
    project_id: str,
    doc_id: str,
    chunks: Sequence[Dict[str, Any]],
    embeddings: Optional[Sequence[Optional[Sequence[float]]]] = None,
) -> int:
    """把文档切片写入内存索引；embeddings 为 None 时使用关键词检索。"""
    if not chunks:
        return 0
    vectors: List[Optional[List[float]]]
    if embeddings is None:
        vectors = [None] * len(chunks)
    else:
        vectors = [
            [float(value) for value in vector] if vector is not None else None
            for vector in embeddings
        ]
        if len(vectors) != len(chunks):
            raise VectorStoreError("切片数与向量数不一致。")

    with _lock:
        # 同一文档重新入库时替换旧切片，避免重复命中。
        for chunk_id, record in list(_chunks.items()):
            if record.get("project_id") == project_id and record.get("doc_id") == doc_id:
                _chunks.pop(chunk_id, None)
        for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
            chunk_id = f"{doc_id}:{index:04d}"
            metadata = {
                "project_id": project_id,
                "doc_id": doc_id,
                **chunk.get("metadata", {}),
            }
            metadata = {
                key: value for key, value in metadata.items() if value is not None
            }
            _chunks[chunk_id] = {
                "chunk_id": chunk_id,
                "project_id": project_id,
                "doc_id": doc_id,
                "text": str(chunk.get("text", "")),
                "metadata": metadata,
                "embedding": vector,
            }
        try:
            _save()
        except Exception as exc:  # noqa: BLE001
            log.exception("内存索引快照写入失败")
            raise VectorStoreError(f"索引快照写入失败：{exc}") from exc
        return len(chunks)


def _terms(query: str) -> List[str]:
    terms: List[str] = []
    for part in _LEXICAL_RE.findall(str(query or "").lower()):
        if re.search(r"[\u4e00-\u9fff]", part):
            cleaned = "".join(char for char in part if char not in _STOP_CHARS)
            if len(cleaned) >= 2:
                for size in (2, 3):
                    for index in range(0, len(cleaned) - size + 1):
                        terms.append(cleaned[index:index + size])
        else:
            terms.append(part)
    return list(dict.fromkeys(terms))


def _lexical_score(query: str, text: str) -> float:
    terms = _terms(query)
    if not terms:
        return 0.0
    lowered = str(text or "").lower()
    hits = sum(1 for term in terms if term in lowered)
    if not hits:
        return 0.0
    return min(0.95, 0.45 + min(hits, 5) * 0.1)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    dot = sum(float(left[i]) * float(right[i]) for i in range(length))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left[:length]))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right[:length]))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def query_chunks(
    project_id: str,
    query_embedding: Optional[Sequence[float]] = None,
    query_text: str = "",
    top_k: int = 5,
    min_score: float = 0.0,
) -> List[Dict[str, Any]]:
    """在内存中按项目检索；有向量用余弦，无向量/无切片向量时用关键词。"""
    matches: List[Dict[str, Any]] = []
    with _lock:
        records = [
            dict(record)
            for record in _chunks.values()
            if record.get("project_id") == project_id
        ]
    for record in records:
        if query_embedding and record.get("embedding"):
            score = _cosine(query_embedding, record["embedding"])
        else:
            score = _lexical_score(query_text, record.get("text", ""))
        score = round(score, 4)
        if score < min_score:
            continue
        matches.append(
            {
                "chunk_id": record["chunk_id"],
                "text": record.get("text", ""),
                "score": score,
                "metadata": record.get("metadata", {}),
            }
        )
    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:top_k]


def delete_project(project_id: str) -> int:
    with _lock:
        ids = [
            chunk_id
            for chunk_id, record in _chunks.items()
            if record.get("project_id") == project_id
        ]
        for chunk_id in ids:
            _chunks.pop(chunk_id, None)
        if ids:
            try:
                _save()
            except Exception as exc:  # noqa: BLE001
                raise VectorStoreError(f"索引快照写入失败：{exc}") from exc
        return len(ids)


def _chunk_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "chunk_id": record["chunk_id"],
        "text": record.get("text", ""),
        "metadata": dict(record.get("metadata", {})),
    }


def get_source_context(project_id: str, chunk_id: str) -> Optional[Dict[str, Any]]:
    """返回命中切片及同文档前/后各一个切片。"""
    with _lock:
        current = _chunks.get(chunk_id)
        if not current or current.get("project_id") != project_id:
            return None
        doc_id = current.get("doc_id")
        records = [
            dict(record)
            for record in _chunks.values()
            if record.get("project_id") == project_id
            and record.get("doc_id") == doc_id
        ]
    records.sort(key=lambda item: int(item.get("metadata", {}).get("chunk_index", 0)))
    index = next(
        (pos for pos, record in enumerate(records) if record["chunk_id"] == chunk_id),
        None,
    )
    if index is None:
        return None
    return {
        "current": _chunk_payload(records[index]),
        "before": _chunk_payload(records[index - 1]) if index > 0 else None,
        "after": (
            _chunk_payload(records[index + 1])
            if index + 1 < len(records)
            else None
        ),
    }


def delete_document(doc_id: str) -> None:
    with _lock:
        ids = [
            chunk_id
            for chunk_id, record in _chunks.items()
            if record.get("doc_id") == doc_id
        ]
        for chunk_id in ids:
            _chunks.pop(chunk_id, None)
        if ids:
            try:
                _save()
            except Exception as exc:  # noqa: BLE001
                log.warning("索引快照写入失败 %s：%s", doc_id, exc)


def collection_count() -> int:
    with _lock:
        return len(_chunks)
