# -*- coding: utf-8 -*-
"""文档登记数据层。

原始文件保存在 storage/uploads/，文档元数据持久化到 storage/documents.json。
Chroma 只保存切片向量；本模块负责文档列表、状态和失败信息。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import DOCUMENTS_FILE


log = logging.getLogger("aps-workbench.document-store")

_lock = threading.RLock()
_documents: Dict[str, Dict[str, Any]] = {}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load_documents() -> Dict[str, Dict[str, Any]]:
    if not DOCUMENTS_FILE.exists():
        return {}
    try:
        with DOCUMENTS_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("documents.json 顶层必须是对象")
        return {key: value for key, value in data.items() if isinstance(value, dict)}
    except Exception as exc:  # noqa: BLE001
        backup = DOCUMENTS_FILE.with_name(
            f"documents.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        )
        try:
            DOCUMENTS_FILE.replace(backup)
            log.error("documents.json 解析失败，已备份到 %s：%s", backup, exc)
        except OSError:
            log.exception("documents.json 解析失败，且无法备份")
        return {}


def _save_documents() -> None:
    DOCUMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DOCUMENTS_FILE.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(_documents, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, DOCUMENTS_FILE)


_documents = _load_documents()


def _copy(value: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(value)


def create_document(
    project_id: str,
    file_name: str,
    file_type: str,
    stage: int,
    size_bytes: int,
    stored_path: str,
) -> Dict[str, Any]:
    """创建 processing 状态文档记录。"""
    timestamp = now_iso()
    doc_id = "doc_" + uuid.uuid4().hex[:12]
    record = {
        "doc_id": doc_id,
        "project_id": project_id,
        "file": file_name,
        "file_type": file_type,
        "stage": stage,
        "status": "processing",
        "chunks_count": 0,
        "summary": "",
        "size_bytes": size_bytes,
        "stored_path": stored_path,
        "created_at": timestamp,
        "indexed_at": None,
        "error": None,
    }
    with _lock:
        _documents[doc_id] = record
        _save_documents()
        return _copy(record)


def update_document(doc_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """更新文档字段并持久化；不存在时返回 None。"""
    with _lock:
        record = _documents.get(doc_id)
        if not record:
            return None
        record.update(fields)
        _save_documents()
        return _copy(record)


def mark_failed(doc_id: str, code: str, message: str) -> Optional[Dict[str, Any]]:
    """标记文档失败，并写入结构化错误。"""
    return update_document(
        doc_id,
        status="failed",
        error={"code": code, "message": message},
        indexed_at=None,
    )


def get_document(doc_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        record = _documents.get(doc_id)
        return _copy(record) if record else None


def list_documents(project_id: str) -> List[Dict[str, Any]]:
    """按创建时间倒序返回项目文档。"""
    with _lock:
        items = [
            record
            for record in _documents.values()
            if record.get("project_id") == project_id
        ]
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return [_copy(item) for item in items]


def count_indexed(project_id: str) -> int:
    """返回项目已成功入库的文档数。"""
    with _lock:
        return sum(
            1
            for record in _documents.values()
            if record.get("project_id") == project_id
            and record.get("status") == "indexed"
        )


def public_document(record: Dict[str, Any]) -> Dict[str, Any]:
    """移除内部存储路径，返回 API 可见字段。"""
    return {
        "doc_id": record.get("doc_id"),
        "file": record.get("file", ""),
        "file_type": record.get("file_type", ""),
        "stage": record.get("stage"),
        "status": record.get("status", "failed"),
        "chunks_count": record.get("chunks_count", 0),
        "summary": record.get("summary", ""),
        "size_bytes": record.get("size_bytes", 0),
        "created_at": record.get("created_at"),
        "indexed_at": record.get("indexed_at"),
        "error": record.get("error"),
    }


def delete_project_records(project_id: str) -> List[Dict[str, Any]]:
    """删除项目文档登记，返回被移除记录；保存失败时回滚内存。"""
    with _lock:
        records = [
            _copy(record)
            for record in _documents.values()
            if record.get("project_id") == project_id
        ]
        if not records:
            return []
        previous = _documents.copy()
        for record in records:
            _documents.pop(record["doc_id"], None)
        try:
            _save_documents()
        except Exception:
            _documents.clear()
            _documents.update(previous)
            raise
        return records


def count_for_project(project_id: str) -> int:
    with _lock:
        return sum(
            1
            for record in _documents.values()
            if record.get("project_id") == project_id
        )
