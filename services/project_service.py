# -*- coding: utf-8 -*-
"""项目服务：项目 CRUD、上下文、阶段切换与归档的业务校验。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import MAX_ARCHIVE_CHARS, STAGE_IDS
from data import context_store
from errors import ApiError
from services import gate_service, llm_service, project_deletion_service, template_service
from services.stage_prompts import STAGES
from services.text_utils import summarize_text


def _get_or_raise(project_id: str) -> Dict[str, Any]:
    project = context_store.get_project(project_id)
    if not project:
        raise ApiError(
            "PROJECT_NOT_FOUND",
            "项目不存在，请刷新页面或重新创建项目。",
            404,
        )
    return project


def _legacy_messages(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为仍使用 messages 字段的旧客户端生成只读兼容视图。"""
    messages: List[Dict[str, Any]] = []
    for item in history:
        role = item.get("role")
        message = {
            "role": role,
            "content": item.get("content", ""),
            "ts": item.get("timestamp"),
        }
        if role == "user":
            message["display"] = item.get("content", "")
        if role == "assistant":
            message["sources"] = item.get("sources", [])
        messages.append(message)
    return messages


def _context_project(project: Dict[str, Any]) -> Dict[str, Any]:
    """在 API 副本上补充 messages 兼容别名，不写回 storage。"""
    for stage in project.get("stages", {}).values():
        stage["messages"] = _legacy_messages(
            stage.get("conversation_history", [])
        )
    return project


def create_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    """创建项目并返回项目对象。"""
    client_name = str(payload.get("client_name", "")).strip()
    if not client_name:
        raise ApiError("VALIDATION_ERROR", "请填写客户名称。", 400)
    if len(client_name) > 120:
        raise ApiError("VALIDATION_ERROR", "客户名称过长。", 400)

    template = None
    template_id = str(payload.get("template_id", "")).strip()
    if template_id:
        template = template_service.resolve_template(template_id)

    project = context_store.create_project(
        {
            "client_name": client_name,
            "industry": payload.get("industry", ""),
            "production_mode": payload.get("production_mode", ""),
            "pain_points": payload.get("pain_points", ""),
        }
    )
    if template:
        try:
            project = template_service.inject_initial_context(
                project["id"], template
            )
        except Exception:
            context_store.remove_project(project["id"])
            raise
    return project


def update_project(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """PUT 保留原有项目信息更新契约（含 pain_points）。"""
    _get_or_raise(project_id)
    project_deletion_service.ensure_not_deleting(project_id)
    fields: Dict[str, Any] = {}
    for field in ("client_name", "industry", "production_mode", "pain_points"):
        if field in payload:
            fields[field] = payload.get(field, "")
    if "client_name" in fields and not str(fields["client_name"]).strip():
        raise ApiError("VALIDATION_ERROR", "客户名称不能为空。", 400)
    if len(str(fields.get("client_name", ""))) > 120:
        raise ApiError("VALIDATION_ERROR", "客户名称过长。", 400)

    project = context_store.update_project_fields(project_id, fields)
    if not project:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    return project


def patch_project(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH 只允许客户名、行业、生产模式。"""
    _get_or_raise(project_id)
    project_deletion_service.ensure_not_deleting(project_id)
    allowed = {"client_name", "industry", "production_mode"}
    unknown = set(payload) - allowed
    if unknown:
        raise ApiError(
            "VALIDATION_ERROR",
            f"不支持修改字段：{', '.join(sorted(unknown))}。",
            400,
        )
    if not payload:
        raise ApiError("VALIDATION_ERROR", "至少提供一个可修改字段。", 400)
    fields: Dict[str, Any] = {}
    for field in allowed:
        if field in payload:
            fields[field] = str(payload.get(field, "")).strip()
    if "client_name" in fields:
        if not fields["client_name"]:
            raise ApiError("VALIDATION_ERROR", "客户名称不能为空。", 400)
        if len(fields["client_name"]) > 80:
            raise ApiError("VALIDATION_ERROR", "客户名称不能超过 80 字符。", 400)
    for field in ("industry", "production_mode"):
        if field in fields and len(fields[field]) > 80:
            raise ApiError(
                "VALIDATION_ERROR",
                f"{field} 不能超过 80 字符。",
                400,
            )

    project = context_store.update_project_fields(project_id, fields)
    if not project:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    return project


def get_context(project_id: str) -> Dict[str, Any]:
    """返回上下文；阶段包含 conversation_history 与 messages 兼容别名。"""
    project = _get_or_raise(project_id)
    return {
        "project": _context_project(project),
        "stages": STAGES,
        "llm_mode": llm_service.mode(),
    }


def list_projects() -> List[Dict[str, Any]]:
    return context_store.list_project_summaries()


def switch_stage(project_id: str, stage: int) -> Dict[str, Any]:
    """切换当前阶段；保留该阶段对话历史，切回后可恢复。"""
    _get_or_raise(project_id)
    project_deletion_service.ensure_not_deleting(project_id)
    if stage not in STAGE_IDS:
        raise ApiError("INVALID_STAGE", "阶段参数必须是 1-5。", 400)
    project = context_store.set_active_stage(project_id, stage, reset_history=False)
    if not project:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    return project


def archive_stage(
    project_id: str,
    stage: int,
    content: str,
    confirmations: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """归档阶段内容，并执行阶段一/三人工确认门禁。"""
    project = _get_or_raise(project_id)
    project_deletion_service.ensure_not_deleting(project_id)
    if stage not in STAGE_IDS:
        raise ApiError("INVALID_STAGE", "阶段参数必须是 1-5。", 400)
    value = str(content or "").strip()
    if not value:
        raise ApiError("VALIDATION_ERROR", "归档内容不能为空。", 400)
    if len(value) > MAX_ARCHIVE_CHARS:
        raise ApiError("VALIDATION_ERROR", "归档内容过长，请精简后再试。", 400)

    gate_confirmation = gate_service.validate_archive(project, stage, confirmations)
    if stage == 5:
        value = gate_service.ensure_stage5_checklist(value)

    stage_state = project["stages"][str(stage)]
    messages = stage_state.get("conversation_history", [])
    last_user = next(
        (
            message
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        None,
    )
    summary_source = ""
    if last_user:
        summary_source = last_user.get("content") or ""
    if not summary_source:
        summary_source = value

    updated = context_store.archive_stage(
        project_id,
        stage,
        value,
        last_summary=summarize_text(summary_source),
        gate_confirmation=gate_confirmation,
    )
    if not updated:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    return updated


def clear_conversation(project_id: str, stage: int) -> Dict[str, Any]:
    """清空指定阶段对话历史，保留归档；重算项目最近摘要。"""
    project = _get_or_raise(project_id)
    project_deletion_service.ensure_not_deleting(project_id)
    if stage not in STAGE_IDS:
        raise ApiError("INVALID_STAGE", "阶段参数必须是 1-5。", 400)
    updated = context_store.clear_conversation(project_id, stage)
    if not updated:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)

    latest_user = None
    latest_timestamp = ""
    for stage_state in updated.get("stages", {}).values():
        for message in stage_state.get("conversation_history", []):
            if message.get("role") != "user":
                continue
            timestamp = str(message.get("timestamp", ""))
            if timestamp >= latest_timestamp:
                latest_timestamp = timestamp
                latest_user = message
    summary = summarize_text(latest_user.get("content", "")) if latest_user else ""
    updated = context_store.set_last_summary(project_id, summary) or updated
    return _context_project(updated)
