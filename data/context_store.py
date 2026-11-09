# -*- coding: utf-8 -*-
"""项目上下文数据层。

封装 projects.json 的内存缓存、线程锁、原子写入和项目/阶段状态变更。
阶段对话统一持久化到 conversation_history；旧 messages 在加载时迁移。
服务层只通过本模块读写项目，不直接操作 JSON 文件。
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

from config import PROJECTS_FILE, STAGE_IDS, STATUS_LABELS


log = logging.getLogger("aps-workbench.context-store")

_lock = threading.RLock()
_projects: Dict[str, Dict[str, Any]] = {}
_needs_migration = False


def now_iso() -> str:
    """返回带本地时区的 ISO 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_stage_state() -> Dict[str, Any]:
    return {
        "status": "not_started",
        "conversation_history": [],
        "initial_context": None,
        "gate_confirmations": [],
        "archive": {
            "content": "",
            "archived_at": None,
            "version": 0,
        },
    }


def _new_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_iso()
    return {
        "id": "prj_" + uuid.uuid4().hex[:12],
        "record_type": "delivery",
        "client_name": str(payload.get("client_name", "")).strip(),
        "industry": str(payload.get("industry", "")).strip(),
        "production_mode": str(payload.get("production_mode", "")).strip(),
        "pain_points": str(payload.get("pain_points", "")).strip(),
        "active_stage": 1,
        "stages": {str(stage_id): _new_stage_state() for stage_id in STAGE_IDS},
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_active_at": timestamp,
        "last_summary": "",
        "post_launch": {"unlocked_at": None, "issues": []},
    }


def _normalise_message(message: Any) -> Optional[Dict[str, Any]]:
    """把旧 messages 或新 conversation_history 条目规范化为统一结构。"""
    if not isinstance(message, dict):
        return None
    role = str(message.get("role", "")).strip()
    if role not in ("user", "assistant"):
        return None
    content = str(message.get("content", "")).strip()
    if role == "user":
        # 旧数据中 display 是界面展示文本，content 可能是拼装后的内部 prompt。
        display = str(message.get("display") or "").strip()
        if display:
            content = display
    if not content:
        return None
    timestamp = str(
        message.get("timestamp") or message.get("ts") or now_iso()
    ).strip()
    item: Dict[str, Any] = {
        "role": role,
        "content": content,
        "timestamp": timestamp,
    }
    if role == "assistant":
        sources = message.get("sources")
        item["sources"] = (
            [copy.deepcopy(source) for source in sources if isinstance(source, dict)]
            if isinstance(sources, list)
            else []
        )
    return item


def _normalise_stage(stage_state: Any) -> Dict[str, Any]:
    if not isinstance(stage_state, dict):
        stage_state = _new_stage_state()
    stage_state.setdefault("status", "not_started")
    if stage_state["status"] not in STATUS_LABELS:
        stage_state["status"] = "not_started"

    raw_history = stage_state.get("conversation_history")
    if not isinstance(raw_history, list):
        raw_history = stage_state.get("messages")
    if not isinstance(raw_history, list):
        raw_history = []
    history = [
        item
        for item in (_normalise_message(message) for message in raw_history)
        if item is not None
    ]
    stage_state["conversation_history"] = history
    stage_state.pop("messages", None)

    initial_context = stage_state.get("initial_context")
    if not isinstance(initial_context, dict) or not str(
        initial_context.get("content", "")
    ).strip():
        initial_context = None
    stage_state["initial_context"] = initial_context

    gates = stage_state.get("gate_confirmations")
    if not isinstance(gates, list):
        gates = []
    stage_state["gate_confirmations"] = [
        gate for gate in gates if isinstance(gate, dict)
    ]

    archive = stage_state.get("archive")
    if not isinstance(archive, dict):
        archive = {
            "content": str(archive or ""),
            "archived_at": None,
            "version": 0,
        }
    archive.setdefault("content", "")
    archive.setdefault("archived_at", None)
    archive.setdefault("version", 0)
    stage_state["archive"] = archive
    return stage_state


def _normalise_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = str(issue.get("created_at") or now_iso())
    issue.setdefault("issue_id", "iss_" + uuid.uuid4().hex[:10])
    issue.setdefault("description", "")
    issue.setdefault("severity", "medium")
    issue.setdefault("status", "pending")
    issue.setdefault("created_at", timestamp)
    issue.setdefault("updated_at", timestamp)
    return issue


def _normalise_project(project: Dict[str, Any]) -> Dict[str, Any]:
    """兼容旧数据，保证五阶段结构和时间字段完整。"""
    project.setdefault("id", "prj_" + uuid.uuid4().hex[:12])
    project.setdefault("record_type", "delivery")
    project.setdefault("client_name", "")
    project.setdefault("industry", "")
    project.setdefault("production_mode", "")
    project.setdefault("pain_points", "")
    created_at = str(
        project.get("created_at")
        or project.get("updated_at")
        or project.get("last_active_at")
        or now_iso()
    )
    project["created_at"] = created_at
    project.setdefault("updated_at", created_at)
    project.setdefault("last_active_at", created_at)
    project.setdefault("last_summary", "")

    if project.get("record_type") == "presales":
        project.setdefault("scale", "")
        assessment = project.get("assessment")
        if not isinstance(assessment, dict):
            assessment = {"content": "", "mode": "demo", "generated_at": created_at}
        assessment.setdefault("content", "")
        assessment.setdefault("mode", "demo")
        assessment.setdefault("generated_at", created_at)
        project["assessment"] = assessment
        project.pop("stages", None)
        return project

    project.setdefault("active_stage", 1)
    project.setdefault("stages", {})
    post_launch = project.get("post_launch")
    if not isinstance(post_launch, dict):
        post_launch = {"unlocked_at": None, "issues": []}
    post_launch.setdefault("unlocked_at", None)
    issues = post_launch.get("issues")
    if not isinstance(issues, list):
        issues = []
    post_launch["issues"] = [
        _normalise_issue(issue) for issue in issues if isinstance(issue, dict)
    ]
    project["post_launch"] = post_launch

    for stage_id in STAGE_IDS:
        key = str(stage_id)
        project["stages"][key] = _normalise_stage(project["stages"].get(key))
    return project


def _load_projects() -> Dict[str, Dict[str, Any]]:
    global _needs_migration
    _needs_migration = False
    if not PROJECTS_FILE.exists():
        return {}
    try:
        with PROJECTS_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("projects.json 顶层必须是对象")
        for project in data.values():
            if not isinstance(project, dict):
                continue
            if any(
                field not in project
                for field in (
                    "created_at",
                    "last_active_at",
                    "last_summary",
                    "record_type",
                )
            ):
                _needs_migration = True
            if (
                project.get("record_type", "delivery") == "delivery"
                and "post_launch" not in project
            ):
                _needs_migration = True
            for stage in (project.get("stages") or {}).values():
                if not isinstance(stage, dict):
                    _needs_migration = True
                    break
                if (
                    "messages" in stage
                    or "conversation_history" not in stage
                    or "initial_context" not in stage
                    or "gate_confirmations" not in stage
                ):
                    _needs_migration = True
                    break
        return {
            project_id: _normalise_project(project)
            for project_id, project in data.items()
            if isinstance(project, dict)
        }
    except Exception as exc:  # noqa: BLE001 - 启动时尽量容错
        backup = PROJECTS_FILE.with_name(
            f"projects.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        )
        try:
            PROJECTS_FILE.replace(backup)
            log.error("projects.json 解析失败，已备份到 %s：%s", backup, exc)
        except OSError:
            log.exception("projects.json 解析失败，且无法备份")
        return {}


def _save_projects() -> None:
    if not isinstance(_projects, dict):
        raise RuntimeError("projects 内存结构必须是字典，拒绝写入")
    PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = PROJECTS_FILE.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(_projects, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, PROJECTS_FILE)


def _copy(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


_projects = _load_projects()

if _needs_migration:
    try:
        _save_projects()
    except OSError:
        log.exception("旧项目字段迁移写回失败")


def list_project_summaries() -> List[Dict[str, Any]]:
    """返回按更新时间倒序的项目摘要。"""
    with _lock:
        items = sorted(
            (
                item
                for item in _projects.values()
                if item.get("record_type", "delivery") == "delivery"
            ),
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )
        return [
            {
                "id": project["id"],
                "client_name": project.get("client_name", ""),
                "industry": project.get("industry", ""),
                "production_mode": project.get("production_mode", ""),
                "updated_at": project.get("updated_at", ""),
                "statuses": {
                    key: value.get("status", "not_started")
                    for key, value in project.get("stages", {}).items()
                },
            }
            for project in items
        ]


def list_all_projects() -> List[Dict[str, Any]]:
    """返回交付项目副本，供仪表盘和模板使用。"""
    with _lock:
        return [
            _copy(project)
            for project in _projects.values()
            if project.get("record_type", "delivery") == "delivery"
        ]


def create_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    """创建并持久化项目；返回项目副本。"""
    with _lock:
        project = _new_project(payload)
        _projects[project["id"]] = project
        _save_projects()
        return _copy(project)


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 返回项目副本；不存在时返回 None。"""
    with _lock:
        project = _projects.get(project_id)
        return _copy(project) if project else None


def update_project_fields(
    project_id: str, fields: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """更新项目基本信息；由服务层负责字段校验。"""
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        for field in ("client_name", "industry", "production_mode", "pain_points"):
            if field in fields:
                project[field] = str(fields.get(field, "")).strip()
        project["updated_at"] = now_iso()
        _save_projects()
        return _copy(project)


def set_active_stage(
    project_id: str, stage: int, reset_history: bool = False
) -> Optional[Dict[str, Any]]:
    """切换当前阶段；默认保留该阶段对话历史。"""
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        stage_state = project["stages"][str(stage)]
        if reset_history:
            stage_state["conversation_history"] = []
        project["active_stage"] = stage
        project["updated_at"] = now_iso()
        _save_projects()
        return _copy(project)


def archive_stage(
    project_id: str,
    stage: int,
    content: str,
    last_summary: Optional[str] = None,
    gate_confirmation: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """写入阶段归档内容并更新状态、最近活跃时间和摘要。"""
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        stage_state = project["stages"][str(stage)]
        previous = stage_state.get("archive", {})
        version = (
            int(previous.get("version", 0)) + 1
            if isinstance(previous, dict)
            else 1
        )
        timestamp = now_iso()
        stage_state["archive"] = {
            "content": content,
            "archived_at": timestamp,
            "version": version,
        }
        stage_state["status"] = "archived"
        if gate_confirmation:
            gates = stage_state.setdefault("gate_confirmations", [])
            gates.append(copy.deepcopy(gate_confirmation))
        project["updated_at"] = timestamp
        project["last_active_at"] = timestamp
        if last_summary is not None:
            project["last_summary"] = last_summary
        if stage == 5:
            post_launch = project.setdefault(
                "post_launch", {"unlocked_at": None, "issues": []}
            )
            if not post_launch.get("unlocked_at"):
                post_launch["unlocked_at"] = timestamp
        _save_projects()
        return _copy(project)


def append_exchange(
    project_id: str,
    stage: int,
    user_message: Dict[str, Any],
    assistant_message: Dict[str, Any],
    replace_history: Optional[List[Dict[str, Any]]] = None,
    last_summary: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """追加一轮对话，并更新最近活跃时间与摘要。"""
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        stage_state = project["stages"][str(stage)]
        if replace_history is not None:
            stage_state["conversation_history"] = [
                item
                for item in (
                    _normalise_message(message) for message in replace_history
                )
                if item is not None
            ]
        history = stage_state.setdefault("conversation_history", [])
        user_item = _normalise_message(user_message)
        assistant_item = _normalise_message(assistant_message)
        if not user_item or not assistant_item:
            return None
        history.append(user_item)
        history.append(assistant_item)
        if stage_state.get("status") in ("not_started", "archived"):
            stage_state["status"] = "in_progress"
        timestamp = now_iso()
        project["active_stage"] = stage
        project["updated_at"] = timestamp
        project["last_active_at"] = timestamp
        if last_summary is not None:
            project["last_summary"] = last_summary
        _save_projects()
        return _copy(project)


def replace_last_assistant(
    project_id: str,
    stage: int,
    assistant_message: Dict[str, Any],
    last_summary: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """替换最后一条 assistant 消息；调用方需保证历史以 assistant 结尾。"""
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        stage_state = project["stages"][str(stage)]
        history = stage_state.get("conversation_history") or []
        if not history or history[-1].get("role") != "assistant":
            return None
        assistant_item = _normalise_message(assistant_message)
        if not assistant_item:
            return None
        history[-1] = assistant_item
        timestamp = now_iso()
        project["active_stage"] = stage
        project["updated_at"] = timestamp
        project["last_active_at"] = timestamp
        if last_summary is not None:
            project["last_summary"] = last_summary
        _save_projects()
        return _copy(project)


def clear_conversation(
    project_id: str, stage: int
) -> Optional[Dict[str, Any]]:
    """清空指定阶段对话历史；归档内容不变。"""
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        stage_state = project["stages"][str(stage)]
        stage_state["conversation_history"] = []
        archive = stage_state.get("archive") or {}
        if str(archive.get("content", "")).strip():
            stage_state["status"] = "archived"
        else:
            stage_state["status"] = "not_started"
        project["updated_at"] = now_iso()
        _save_projects()
        return _copy(project)


def remove_project(project_id: str) -> Optional[Dict[str, Any]]:
    """原子移除 projects.json 中的项目记录，返回被删除的项目副本。"""
    with _lock:
        project = _projects.pop(project_id, None)
        if not project:
            return None
        try:
            _save_projects()
        except Exception:
            _projects[project_id] = project
            raise
        return _copy(project)


def set_last_summary(project_id: str, summary: str) -> Optional[Dict[str, Any]]:
    """只更新项目最近摘要，用于清空历史后的摘要重算。"""
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        project["last_summary"] = summary
        _save_projects()
        return _copy(project)


def set_initial_context(
    project_id: str, stage: int, initial_context: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """写入历史模板带入的初始上下文，不改变阶段归档状态。"""
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        stage_state = project["stages"][str(stage)]
        stage_state["initial_context"] = copy.deepcopy(initial_context)
        project["updated_at"] = now_iso()
        _save_projects()
        return _copy(project)


def create_presales(
    fields: Dict[str, Any], assessment: Dict[str, Any], mode: str
) -> Dict[str, Any]:
    """创建一条轻量售前评估记录，不进入五阶段。"""
    timestamp = now_iso()
    presales_id = "presales_" + uuid.uuid4().hex[:12]
    record = {
        "id": presales_id,
        "record_type": "presales",
        "industry": str(fields.get("industry", "")).strip(),
        "production_mode": str(fields.get("production_mode", "")).strip(),
        "pain_points": str(fields.get("pain_points", "")).strip(),
        "scale": str(fields.get("scale", "")).strip(),
        "assessment": {
            "content": str(assessment.get("content", "")),
            "mode": mode,
            "generated_at": timestamp,
        },
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with _lock:
        _projects[presales_id] = record
        _save_projects()
        return _copy(record)


def get_presales(presales_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        record = _projects.get(presales_id)
        if not record or record.get("record_type") != "presales":
            return None
        return _copy(record)


def list_presales() -> List[Dict[str, Any]]:
    with _lock:
        items = [
            _copy(record)
            for record in _projects.values()
            if record.get("record_type") == "presales"
        ]
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return items


def _is_unlocked(project: Dict[str, Any]) -> bool:
    post_launch = project.get("post_launch") or {}
    if post_launch.get("unlocked_at"):
        return True
    return (
        (project.get("stages", {}).get("5", {}) or {}).get("status")
        == "archived"
    )


def ensure_post_launch_unlocked(project_id: str) -> Optional[Dict[str, Any]]:
    """阶段五已归档时补写解锁时间；返回项目副本。"""
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        if _is_unlocked(project):
            post_launch = project.setdefault(
                "post_launch", {"unlocked_at": None, "issues": []}
            )
            if not post_launch.get("unlocked_at"):
                post_launch["unlocked_at"] = now_iso()
                _save_projects()
        return _copy(project)


def list_issues(project_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        post_launch = project.get("post_launch") or {"unlocked_at": None, "issues": []}
        return {
            "unlocked": _is_unlocked(project),
            "unlocked_at": post_launch.get("unlocked_at"),
            "issues": copy.deepcopy(post_launch.get("issues", [])),
        }


def add_issue(
    project_id: str,
    description: str,
    severity: str,
    status: str,
) -> Optional[Dict[str, Any]]:
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        timestamp = now_iso()
        issue = {
            "issue_id": "iss_" + uuid.uuid4().hex[:10],
            "description": description,
            "severity": severity,
            "status": status,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        post_launch = project.setdefault(
            "post_launch", {"unlocked_at": None, "issues": []}
        )
        post_launch.setdefault("issues", []).append(issue)
        if not post_launch.get("unlocked_at"):
            post_launch["unlocked_at"] = timestamp
        project["updated_at"] = timestamp
        _save_projects()
        return _copy(issue)


def update_issue(
    project_id: str, issue_id: str, fields: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    with _lock:
        project = _projects.get(project_id)
        if not project:
            return None
        post_launch = project.setdefault(
            "post_launch", {"unlocked_at": None, "issues": []}
        )
        issue = next(
            (
                item
                for item in post_launch.get("issues", [])
                if item.get("issue_id") == issue_id
            ),
            None,
        )
        if not issue:
            return None
        for field in ("description", "severity", "status"):
            if field in fields:
                issue[field] = fields[field]
        issue["updated_at"] = now_iso()
        project["updated_at"] = issue["updated_at"]
        _save_projects()
        return _copy(issue)
