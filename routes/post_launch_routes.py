# -*- coding: utf-8 -*-
"""上线后跟踪路由。"""

from __future__ import annotations

from flask import Blueprint, jsonify, send_file

from routes.helpers import json_object, require_project_id
from services import post_launch_service


post_launch_bp = Blueprint("post_launch_api", __name__, url_prefix="/api")


@post_launch_bp.get("/issues/<project_id>")
def get_issues(project_id: str):
    data = post_launch_service.get_issues(project_id)
    return jsonify({"ok": True, **data})


@post_launch_bp.post("/issues/<project_id>")
def add_issue(project_id: str):
    issue = post_launch_service.add_issue(project_id, json_object())
    return jsonify({"ok": True, "issue": issue}), 201


@post_launch_bp.patch("/issues/<project_id>/<issue_id>")
def update_issue(project_id: str, issue_id: str):
    issue = post_launch_service.update_issue(project_id, issue_id, json_object())
    return jsonify({"ok": True, "issue": issue})


@post_launch_bp.post("/chat/issues")
def analyze_issues():
    payload = json_object()
    project_id = require_project_id(payload)
    result = post_launch_service.analyze_issues(
        project_id, str(payload.get("prompt", "")).strip()
    )
    return jsonify({"ok": True, **result})


@post_launch_bp.post("/export/issues")
def export_issues():
    payload = json_object()
    project_id = require_project_id(payload)
    export_file = post_launch_service.export_excel(project_id)
    response = send_file(
        export_file.stream,
        mimetype=export_file.mimetype,
        as_attachment=True,
        download_name=export_file.filename,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
