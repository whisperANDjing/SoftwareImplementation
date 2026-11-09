# -*- coding: utf-8 -*-
"""项目删除服务。

跨 Chroma / 文件 / projects.json 无法使用单一事务，这里使用删除日志、
单项目锁和顺序删除，保证只有全部成功后项目记录才消失。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from config import DELETIONS_DIR, UPLOAD_DIR
from data import assistant_store, context_store, document_store, vector_store
from errors import ApiError


log = logging.getLogger("aps-workbench.project-deletion")

_lock = threading.RLock()
_deleting: set[str] = set()


def is_deleting(project_id: str) -> bool:
    with _lock:
        return project_id in _deleting


def ensure_not_deleting(project_id: str) -> None:
    if is_deleting(project_id):
        raise ApiError(
            "PROJECT_BUSY",
            "项目正在删除，暂时不能执行该操作。",
            409,
        )


def _journal_path(project_id: str) -> Path:
    return DELETIONS_DIR / f"{project_id}.json"


def _write_journal(project_id: str, data: Dict[str, Any]) -> None:
    DELETIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _journal_path(project_id)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _read_journal(project_id: str) -> Optional[Dict[str, Any]]:
    path = _journal_path(project_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        log.exception("读取删除日志失败：%s", path)
        return None


def _remove_journal(project_id: str) -> None:
    try:
        _journal_path(project_id).unlink(missing_ok=True)
    except OSError:
        log.warning("删除日志清理失败：%s", _journal_path(project_id))


def _safe_upload_path(stored_path: str) -> Optional[Path]:
    if not stored_path:
        return None
    path = Path(stored_path).resolve()
    root = UPLOAD_DIR.resolve()
    if path != root and root not in path.parents:
        log.warning("忽略越界上传路径：%s", path)
        return None
    return path


def _delete_uploads(records: list[Dict[str, Any]]) -> int:
    removed = 0
    for record in records:
        path = _safe_upload_path(str(record.get("stored_path", "")))
        if not path:
            continue
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except OSError as exc:
            raise ApiError(
                "DOCUMENT_DELETE_FAILED",
                f"删除上传文件失败：{path.name}。",
                500,
                {"file": path.name},
            ) from exc
    return removed


def _snapshot(project: Dict[str, Any], documents: list[Dict[str, Any]]) -> Dict[str, Any]:
    conversation_messages = sum(
        len(stage.get("conversation_history", []))
        for stage in project.get("stages", {}).values()
    )
    return {
        "project_id": project["id"],
        "project": project,
        "documents": documents,
        "assistant_messages": assistant_store.get_messages(project["id"]),
        "counts": {
            "conversation_messages": conversation_messages,
            "documents": len(documents),
        },
        "step": "prepared",
    }


def _fail(project_id: str, journal: Dict[str, Any], step: str, code: str, message: str):
    journal["step"] = step
    journal["failed_step"] = step
    journal["error"] = message
    try:
        _write_journal(project_id, journal)
    except OSError:
        log.exception("写入删除失败日志失败：%s", project_id)
    raise ApiError(code, message, 500, {"failed_step": step, "retryable": True})


def _execute_delete(project: Dict[str, Any]) -> Dict[str, Any]:
    project_id = project["id"]
    documents = document_store.list_documents(project_id)
    journal = _snapshot(project, documents)
    _write_journal(project_id, journal)

    # 1. Chroma 向量
    try:
        vectors = vector_store.delete_project(project_id)
        journal["step"] = "documents"
        _write_journal(project_id, journal)
    except vector_store.VectorStoreError as exc:
        _fail(project_id, journal, "vector_store", "VECTOR_DELETE_FAILED", str(exc))

    # 2. 文档登记、原始文件、助手历史
    try:
        uploads = _delete_uploads(documents)
        document_store.delete_project_records(project_id)
        journal["step"] = "assistant"
        _write_journal(project_id, journal)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(
            project_id,
            journal,
            "documents",
            "DOCUMENT_DELETE_FAILED",
            f"删除文档数据失败：{exc}",
        )

    try:
        assistant_messages = assistant_store.delete_project(project_id)
        journal["step"] = "projects_json"
        _write_journal(project_id, journal)
    except Exception as exc:  # noqa: BLE001
        _fail(
            project_id,
            journal,
            "assistant_history",
            "ASSISTANT_HISTORY_DELETE_FAILED",
            f"删除助手历史失败：{exc}",
        )

    # 3. 最后原子移除 projects.json 记录
    try:
        removed = context_store.remove_project(project_id)
        if not removed:
            raise RuntimeError("项目记录不存在")
        journal["step"] = "done"
        _write_journal(project_id, journal)
    except Exception as exc:  # noqa: BLE001
        _fail(
            project_id,
            journal,
            "projects_json",
            "PROJECT_DELETE_FAILED",
            f"删除项目上下文失败：{exc}",
        )

    _remove_journal(project_id)
    return {
        "vectors": vectors,
        "documents": journal["counts"]["documents"],
        "uploads": uploads,
        "assistant_messages": assistant_messages,
        "conversation_messages": journal["counts"]["conversation_messages"],
    }


def delete_project(project_id: str) -> Dict[str, Any]:
    """删除项目及其所有关联数据；成功后才返回。"""
    with _lock:
        if project_id in _deleting:
            raise ApiError(
                "PROJECT_DELETE_IN_PROGRESS",
                "项目正在删除，请勿重复操作。",
                409,
            )
        project = context_store.get_project(project_id)
        if not project:
            raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
        _deleting.add(project_id)
    try:
        deleted = _execute_delete(project)
        return {"project_id": project_id, "deleted": deleted}
    finally:
        with _lock:
            _deleting.discard(project_id)


def recover_pending_deletions() -> None:
    """启动时继续处理未完成的删除日志。"""
    if not DELETIONS_DIR.exists():
        return
    for path in sorted(DELETIONS_DIR.glob("*.json")):
        project_id = path.stem
        journal = _read_journal(project_id)
        if not journal:
            _remove_journal(project_id)
            continue
        project = context_store.get_project(project_id)
        if project:
            try:
                log.info("恢复未完成的项目删除：%s", project_id)
                delete_project(project_id)
            except Exception:  # noqa: BLE001
                log.exception("恢复项目删除失败：%s", project_id)
            continue
        # 项目记录已不存在，清理可能残留在文档/助手历史中的记录。
        try:
            documents = document_store.list_documents(project_id)
            _delete_uploads(documents)
            document_store.delete_project_records(project_id)
            assistant_store.delete_project(project_id)
            vector_store.delete_project(project_id)
            _remove_journal(project_id)
        except Exception:  # noqa: BLE001
            log.exception("清理孤立删除日志失败：%s", project_id)
