# -*- coding: utf-8 -*-
"""历史模板列表与新建项目初始上下文注入。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from data import context_store
from errors import ApiError
from services.stage_prompts import STAGE_BY_ID
from services.text_utils import summarize_text


def _template_record(project: Dict[str, Any], stage: int) -> Optional[Dict[str, Any]]:
    stage_state = project.get("stages", {}).get(str(stage), {})
    if stage_state.get("status") != "archived":
        return None
    archive = stage_state.get("archive") or {}
    content = str(archive.get("content", "")).strip()
    if not content:
        return None
    version = int(archive.get("version", 1) or 1)
    stage_meta = STAGE_BY_ID[stage]
    return {
        "template_id": f"{project['id']}:{stage}:{version}",
        "source_project_id": project["id"],
        "source_project_name": project.get("client_name", ""),
        "stage": stage,
        "stage_name": stage_meta["name"],
        "archived_at": archive.get("archived_at"),
        "version": version,
        "preview": summarize_text(content, 120),
    }


def list_templates() -> List[Dict[str, Any]]:
    templates: List[Dict[str, Any]] = []
    for project in context_store.list_all_projects():
        for stage in (1, 5):
            record = _template_record(project, stage)
            if record:
                templates.append(record)
    templates.sort(key=lambda item: item.get("archived_at") or "", reverse=True)
    return templates


def resolve_template(template_id: str) -> Dict[str, Any]:
    parts = str(template_id or "").split(":")
    if len(parts) != 3:
        raise ApiError("TEMPLATE_NOT_FOUND", "历史模板不存在。", 404)
    project_id, stage_text, version_text = parts
    try:
        stage = int(stage_text)
        version = int(version_text)
    except ValueError as exc:
        raise ApiError("TEMPLATE_NOT_FOUND", "历史模板不存在。", 404) from exc
    if stage not in (1, 5):
        raise ApiError("TEMPLATE_INVALID", "只支持阶段一或阶段五模板。", 400)
    project = context_store.get_project(project_id)
    if not project or project.get("record_type", "delivery") != "delivery":
        raise ApiError("TEMPLATE_NOT_FOUND", "历史模板不存在。", 404)
    record = _template_record(project, stage)
    if not record or record["version"] != version:
        raise ApiError("TEMPLATE_NOT_FOUND", "历史模板不存在或已更新。", 404)
    archive = project["stages"][str(stage)]["archive"]
    return {
        **record,
        "content": str(archive.get("content", "")),
    }


def inject_initial_context(project_id: str, template: Dict[str, Any]) -> Dict[str, Any]:
    initial_context = {
        "content": template["content"],
        "source_template_id": template["template_id"],
        "source_project_id": template["source_project_id"],
        "source_project_name": template.get("source_project_name", ""),
        "source_stage": template["stage"],
        "injected_at": context_store.now_iso(),
    }
    project = context_store.set_initial_context(
        project_id, template["stage"], initial_context
    )
    if not project:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    return project
