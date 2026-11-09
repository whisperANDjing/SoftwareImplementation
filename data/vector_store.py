# -*- coding: utf-8 -*-
"""Chroma 向量库数据层。

使用本地持久化模式，不引入独立服务。一个 collection 通过 project_id 元数据隔离项目。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Sequence

from config import CHROMA_COLLECTION, CHROMA_DIR


log = logging.getLogger("aps-workbench.vector-store")

_lock = threading.RLock()
_client = None
_collection = None


class VectorStoreError(RuntimeError):
    """Chroma 不可用或读写失败。"""


def _get_collection():
    """延迟初始化 Chroma client 与 collection。"""
    global _client, _collection
    with _lock:
        if _collection is not None:
            return _collection
        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover - 依赖缺失时
            raise VectorStoreError(
                "Chroma 未安装，请运行 pip install -r requirements.txt。"
            ) from exc

        try:
            CHROMA_DIR.mkdir(parents=True, exist_ok=True)
            from chromadb.config import Settings

            _client = chromadb.PersistentClient(
                path=str(CHROMA_DIR),
                settings=Settings(anonymized_telemetry=False),
            )
            _collection = _client.get_or_create_collection(
                name=CHROMA_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            return _collection
        except Exception as exc:  # noqa: BLE001
            log.exception("Chroma 初始化失败")
            raise VectorStoreError(f"向量库初始化失败：{exc}") from exc


def add_chunks(
    project_id: str,
    doc_id: str,
    chunks: Sequence[Dict[str, Any]],
    embeddings: Sequence[Sequence[float]],
) -> int:
    """写入文档切片。

    输入：项目 ID、文档 ID、切片列表（含 text/metadata）、向量列表
    输出：写入切片数
    """
    if len(chunks) != len(embeddings):
        raise VectorStoreError("切片数与向量数不一致。")
    if not chunks:
        return 0

    collection = _get_collection()
    ids: List[str] = []
    documents: List[str] = []
    metadatas: List[Dict[str, Any]] = []
    vectors: List[List[float]] = []

    for index, (chunk, vector) in enumerate(zip(chunks, embeddings)):
        metadata = {
            "project_id": project_id,
            "doc_id": doc_id,
            **chunk.get("metadata", {}),
        }
        # Chroma 只接受 str/int/float/bool 元数据，过滤 None 与空值。
        metadata = {
            key: value
            for key, value in metadata.items()
            if value is not None
        }
        ids.append(f"{doc_id}:{index:04d}")
        documents.append(str(chunk.get("text", "")))
        metadatas.append(metadata)
        vectors.append([float(value) for value in vector])

    try:
        # 小批量 upsert，避免单次请求过大。
        batch_size = 100
        for start in range(0, len(ids), batch_size):
            collection.upsert(
                ids=ids[start:start + batch_size],
                documents=documents[start:start + batch_size],
                metadatas=metadatas[start:start + batch_size],
                embeddings=vectors[start:start + batch_size],
            )
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        log.exception("Chroma 写入失败")
        raise VectorStoreError(f"向量库写入失败：{exc}") from exc


def query_chunks(
    project_id: str,
    query_embedding: Sequence[float],
    top_k: int,
    min_score: float,
) -> List[Dict[str, Any]]:
    """按项目检索最相关片段，返回按相似度降序的片段和元数据。"""
    collection = _get_collection()
    try:
        result = collection.query(
            query_embeddings=[[float(value) for value in query_embedding]],
            n_results=max(1, top_k),
            where={"project_id": project_id},
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Chroma 检索失败")
        raise VectorStoreError(f"向量库检索失败：{exc}") from exc

    documents = (result.get("documents") or [[]])[0] or []
    metadatas = (result.get("metadatas") or [[]])[0] or []
    distances = (result.get("distances") or [[]])[0] or []
    ids = (result.get("ids") or [[]])[0] or []

    matches: List[Dict[str, Any]] = []
    for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
        score = max(0.0, min(1.0, 1.0 - float(distance)))
        if score < min_score:
            continue
        meta = dict(metadata or {})
        matches.append(
            {
                "chunk_id": chunk_id,
                "text": text,
                "score": round(score, 4),
                "metadata": meta,
            }
        )
    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:top_k]


def delete_project(project_id: str) -> int:
    """删除项目全部向量，返回删除的切片数。"""
    collection = _get_collection()
    try:
        existing = collection.get(where={"project_id": project_id}, include=["metadatas"])
        ids = existing.get("ids") or []
        if ids:
            collection.delete(where={"project_id": project_id})
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        log.exception("Chroma 项目删除失败")
        raise VectorStoreError(f"向量库项目删除失败：{exc}") from exc


def _chunk_from_result(chunk_id: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "metadata": dict(metadata or {}),
    }


def get_source_context(project_id: str, chunk_id: str) -> Optional[Dict[str, Any]]:
    """返回命中切片及同文档前/后各一个切片。"""
    collection = _get_collection()
    try:
        current = collection.get(
            ids=[chunk_id], include=["documents", "metadatas"]
        )
        ids = current.get("ids") or []
        if not ids:
            return None
        documents = current.get("documents") or []
        metadatas = current.get("metadatas") or []
        metadata = dict(metadatas[0] or {})
        if metadata.get("project_id") != project_id:
            return None
        doc_id = metadata.get("doc_id")
        if not doc_id:
            return None
        doc = collection.get(
            where={"doc_id": doc_id}, include=["documents", "metadatas"]
        )
        doc_ids = doc.get("ids") or []
        doc_docs = doc.get("documents") or []
        doc_metas = doc.get("metadatas") or []
        chunks = [
            _chunk_from_result(item_id, text, meta)
            for item_id, text, meta in zip(doc_ids, doc_docs, doc_metas)
        ]
        chunks.sort(key=lambda item: int(item["metadata"].get("chunk_index", 0)))
        index = next(
            (pos for pos, item in enumerate(chunks) if item["chunk_id"] == chunk_id),
            None,
        )
        if index is None:
            return None
        current_chunk = chunks[index]
        before = chunks[index - 1] if index > 0 else None
        after = chunks[index + 1] if index + 1 < len(chunks) else None
        return {
            "current": current_chunk,
            "before": before,
            "after": after,
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("Chroma 来源详情读取失败")
        raise VectorStoreError(f"来源详情读取失败：{exc}") from exc


def delete_document(doc_id: str) -> None:
    """删除某文档的全部切片；用于失败回滚。"""
    collection = _get_collection()
    try:
        collection.delete(where={"doc_id": doc_id})
    except Exception as exc:  # noqa: BLE001
        log.warning("删除 Chroma 文档切片失败 %s：%s", doc_id, exc)


def collection_count() -> int:
    """返回 collection 当前切片总数，用于健康检查/诊断。"""
    try:
        return int(_get_collection().count())
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError(f"向量库状态读取失败：{exc}") from exc
