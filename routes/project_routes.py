# -*- coding: utf-8 -*-
"""项目、上下文与归档路由。"""

from __future__ import annotations

from flask import Blueprint, jsonify, request, send_file

from config import DEEPSEEK_MODEL
from routes.helpers import json_object, parse_stage, require_project_id
from services import (
    dashboard_service,
    llm_service,
    export_service,
    project_deletion_service,
    project_service,
    source_service,
    template_service,
)
from services.embedding_service import mode as embedding_mode, model_name
from services.stage_prompts import STAGES


project_bp = Blueprint("project_api", __name__, url_prefix="/api")


@project_bp.get("/health")
def health():
    """运行状态与阶段元数据。保留原字段，新增嵌入模型信息。"""
    mode = llm_service.mode()
    return jsonify(
        {
            "ok": True,
            "mode": mode,
            "model": DEEPSEEK_MODEL if mode == "deepseek" else "local-demo",
            "has_api_key": llm_service.has_api_key(),
            "embedding_model": model_name(),
            "embedding_mode": embedding_mode(),
            "stages": STAGES,
        }
    )


@project_bp.get("/projects")
def list_projects():
    return jsonify({"ok": True, "projects": project_service.list_projects()})


@project_bp.get("/dashboard")
def dashboard():
    """首页仪表盘：统计卡片与项目列表，全部实时读取项目上下文。"""
    return jsonify(dashboard_service.get_dashboard())


@project_bp.get("/templates")
def list_templates():
    return jsonify({"ok": True, "templates": template_service.list_templates()})


@project_bp.post("/project")
def create_project():
    payload = json_object()
    project = project_service.create_project(payload)
    # project_id 为新增的便捷字段；project 保留现有契约。
    return jsonify(
        {"ok": True, "project_id": project["id"], "project": project}
    ), 201


@project_bp.put("/project/<project_id>")
def update_project(project_id: str):
    payload = json_object()
    project = project_service.update_project(project_id, payload)
    return jsonify({"ok": True, "project": project})


@project_bp.patch("/project/<project_id>")
def patch_project(project_id: str):
    payload = json_object()
    project = project_service.patch_project(project_id, payload)
    summary = dashboard_service.project_summary(project)
    return jsonify(
        {
            "ok": True,
            "project_id": project["id"],
            "project": project,
            "summary": summary,
        }
    )


@project_bp.delete("/project/<project_id>")
def delete_project(project_id: str):
    result = project_deletion_service.delete_project(project_id)
    return jsonify({"ok": True, **result})


@project_bp.post("/conversation/clear")
def clear_conversation():
    payload = json_object()
    project_id = require_project_id(payload)
    stage = parse_stage(payload.get("stage"))
    project = project_service.clear_conversation(project_id, stage)
    return jsonify(
        {
            "ok": True,
            "project_id": project_id,
            "stage": stage,
            "conversation_history": project["stages"][str(stage)].get(
                "conversation_history", []
            ),
            "project": project,
        }
    )


@project_bp.get("/source/<project_id>")
def get_source(project_id: str):
    chunk_id = str(request.args.get("chunk_id", "")).strip()
    source = source_service.get_source(project_id, chunk_id)
    return jsonify({"ok": True, "source": source})


def _export_response(export_file):
    response = send_file(
        export_file.stream,
        mimetype=export_file.mimetype,
        as_attachment=True,
        download_name=export_file.filename,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@project_bp.post("/export/word")
def export_word():
    payload = json_object()
    project_id = require_project_id(payload)
    stage = parse_stage(payload.get("stage"))
    return _export_response(export_service.export_word(project_id, stage))


@project_bp.post("/export/excel")
def export_excel():
    payload = json_object()
    project_id = require_project_id(payload)
    stage = parse_stage(payload.get("stage"))
    return _export_response(export_service.export_excel(project_id, stage))


@project_bp.get("/context/<project_id>")
def get_context(project_id: str):
    data = project_service.get_context(project_id)
    return jsonify({"ok": True, **data})


@project_bp.post("/stage/switch")
def switch_stage():
    payload = json_object()
    project_id = require_project_id(payload)
    stage = parse_stage(payload.get("stage"))
    project = project_service.switch_stage(project_id, stage)
    return jsonify({"ok": True, "project": project})


@project_bp.post("/archive")
def archive_stage():
    payload = json_object()
    project_id = require_project_id(payload)
    stage = parse_stage(payload.get("stage"))
    project = project_service.archive_stage(
        project_id,
        stage,
        payload.get("content", ""),
        confirmations=payload.get("confirmations"),
    )
    return jsonify({"ok": True, "project": project})
