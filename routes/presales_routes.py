# -*- coding: utf-8 -*-
"""售前快速评估路由。"""

from __future__ import annotations

from flask import Blueprint, jsonify, send_file

from routes.helpers import json_object
from services import presales_service


presales_bp = Blueprint("presales_api", __name__, url_prefix="/api")


@presales_bp.post("/presales")
def create_presales():
    record = presales_service.create_assessment(json_object())
    return jsonify(
        {
            "ok": True,
            "presales_id": record["id"],
            "record": record,
            "assessment": {
                "content": (record.get("assessment") or {}).get("content", ""),
                "mode": (record.get("assessment") or {}).get("mode", "demo"),
            },
        }
    ), 201


@presales_bp.get("/presales")
def list_presales():
    return jsonify({"ok": True, "presales": presales_service.list_assessments()})


@presales_bp.get("/presales/<presales_id>")
def get_presales(presales_id: str):
    record = presales_service.get_assessment(presales_id)
    return jsonify({"ok": True, "record": record})


@presales_bp.post("/presales/<presales_id>/export/word")
def export_presales_word(presales_id: str):
    export_file = presales_service.export_assessment_word(presales_id)
    response = send_file(
        export_file.stream,
        mimetype=export_file.mimetype,
        as_attachment=True,
        download_name=export_file.filename,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
