# -*- coding: utf-8 -*-
"""全局 AI 小助手独立历史存储。

与五阶段工作流的 project["stages"][stage]["messages"] 完全分离，
存放在 storage/assistant_history.json，按 project_id 隔离。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, List

from config import ASSISTANT_HISTORY_FILE, ASSISTANT_MAX_HISTORY_MESSAGES


log = logging.getLogger("aps-workbench.assistant-store")

_lock = threading.RLock()
_histories: Dict[str, List[Dict[str, Any]]] = {}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load() -> Dict[str, List[Dict[str, Any]]]:
    if not ASSISTANT_HISTORY_FILE.exists():
        return {}
    try:
        with ASSISTANT_HISTORY_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("assistant_history.json 顶层必须是对象")
        return {
            str(project_id): [item for item in messages if isinstance(item, dict)]
            for project_id, messages in data.items()
            if isinstance(messages, list)
        }
    except Exception as exc:  # noqa: BLE001
        backup = ASSISTANT_HISTORY_FILE.with_name(
            f"assistant_history.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        )
        try:
            ASSISTANT_HISTORY_FILE.replace(backup)
            log.error("assistant_history.json 解析失败，已备份到 %s：%s", backup, exc)
        except OSError:
            log.exception("assistant_history.json 解析失败，且无法备份")
        return {}


def _save() -> None:
    ASSISTANT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = ASSISTANT_HISTORY_FILE.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(_histories, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, ASSISTANT_HISTORY_FILE)


_histories = _load()


def get_messages(project_id: str) -> List[Dict[str, Any]]:
    """返回助手历史副本；不存在时返回空列表。"""
    with _lock:
        return copy.deepcopy(_histories.get(project_id, []))


def append_exchange(
    project_id: str,
    user_message: Dict[str, Any],
    assistant_message: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """追加一轮助手问答，只保留最近 N 条消息。"""
    with _lock:
        messages = _histories.setdefault(project_id, [])
        messages.append(copy.deepcopy(user_message))
        messages.append(copy.deepcopy(assistant_message))
        if len(messages) > ASSISTANT_MAX_HISTORY_MESSAGES:
            del messages[: len(messages) - ASSISTANT_MAX_HISTORY_MESSAGES]
        _save()
        return copy.deepcopy(messages)


def clear(project_id: str) -> None:
    """清空某项目的助手历史，主要供测试/维护使用。"""
    with _lock:
        _histories.pop(project_id, None)
        _save()


def last_messages(project_id: str, limit: int) -> List[Dict[str, str]]:
    """返回适合 LLM 使用的最近 role/content 列表。"""
    with _lock:
        messages = _histories.get(project_id, [])[-limit:]
    return [
        {"role": item["role"], "content": item["content"]}
        for item in messages
        if item.get("role") in ("user", "assistant") and item.get("content")
    ]


def delete_project(project_id: str) -> int:
    """删除项目助手历史，返回删除的消息数；保存失败时回滚。"""
    with _lock:
        messages = _histories.pop(project_id, None)
        if messages is None:
            return 0
        try:
            _save()
        except Exception:
            _histories[project_id] = messages
            raise
        return len(messages)
