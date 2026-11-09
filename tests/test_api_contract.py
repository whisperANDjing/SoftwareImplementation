# -*- coding: utf-8 -*-
"""API 统一错误格式与请求 ID 测试。"""

from __future__ import annotations

from app import app


def test_api_error_envelope_and_request_id():
    client = app.test_client()
    response = client.post(
        "/api/chat",
        json={"project_id": "missing-project", "message": "测试", "answer_mode": "rag"},
    )
    body = response.get_json()

    assert response.status_code == 404
    assert body["ok"] is False
    assert body["code"] == "PROJECT_NOT_FOUND"
    assert body["message"]
    assert "data" in body
    assert body["error"] == body["message"]
    assert response.headers.get("X-Request-ID")


def test_create_project_returns_project_id_and_dashboard(store):
    client = app.test_client()
    created = client.post(
        "/api/project",
        json={
            "client_name": "仪表盘测试客户",
            "industry": "装备制造",
            "production_mode": "订单型 MTO",
            "pain_points": "插单",
        },
    )
    body = created.get_json()
    assert created.status_code == 201
    assert body["project_id"] == body["project"]["id"]

    dashboard = client.get("/api/dashboard")
    data = dashboard.get_json()
    assert dashboard.status_code == 200
    assert "ok" not in data
    assert data["stats"]["total_projects"] == 1
    assert data["projects"][0]["project_id"] == body["project_id"]
    assert data["projects"][0]["last_summary"] == ""
    assert len(data["projects"][0]["stage_status"]) == 5


def test_dashboard_card_includes_created_at(store):
    client = app.test_client()
    client.post(
        "/api/project",
        json={"client_name": "创建时间测试", "industry": "制造"},
    )
    body = client.get("/api/dashboard").get_json()
    assert body["projects"][0]["created_at"]
