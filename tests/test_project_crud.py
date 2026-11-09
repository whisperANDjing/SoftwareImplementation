# -*- coding: utf-8 -*-
"""项目 PATCH / DELETE 与删除日志测试。"""

from __future__ import annotations

from app import app
from data import assistant_store, context_store, document_store, vector_store
from services import project_deletion_service


def _patch_delete_dependencies(monkeypatch, tmp_path):
    monkeypatch.setattr(project_deletion_service, "DELETIONS_DIR", tmp_path / "deletions")
    monkeypatch.setattr(vector_store, "delete_project", lambda project_id: 3)
    monkeypatch.setattr(document_store, "list_documents", lambda project_id: [])
    monkeypatch.setattr(
        document_store, "delete_project_records", lambda project_id: []
    )
    monkeypatch.setattr(assistant_store, "get_messages", lambda project_id: [])
    monkeypatch.setattr(assistant_store, "delete_project", lambda project_id: 0)


def test_patch_project_updates_summary(store, project):
    client = app.test_client()
    response = client.patch(
        f"/api/project/{project['id']}",
        json={"client_name": "改名后的客户", "industry": "新能源"},
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["project"]["client_name"] == "改名后的客户"
    assert body["summary"]["client_name"] == "改名后的客户"
    assert body["summary"]["industry"] == "新能源"
    assert body["summary"]["created_at"] == body["project"]["created_at"]


def test_patch_project_rejects_unknown_or_empty_fields(store, project):
    client = app.test_client()
    response = client.patch(
        f"/api/project/{project['id']}",
        json={"pain_points": "不允许"},
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "VALIDATION_ERROR"
    response = client.patch(
        f"/api/project/{project['id']}",
        json={"client_name": "   "},
    )
    assert response.status_code == 400


def test_delete_project_removes_record_and_journal(store, project, tmp_path, monkeypatch):
    client = app.test_client()
    _patch_delete_dependencies(monkeypatch, tmp_path)
    store.append_exchange(
        project["id"],
        1,
        {"role": "user", "content": "待删除问题"},
        {"role": "assistant", "content": "待删除回答"},
    )

    response = client.delete(f"/api/project/{project['id']}")
    body = response.get_json()

    assert response.status_code == 200
    assert body["deleted"]["vectors"] == 3
    assert store.get_project(project["id"]) is None
    assert not list((tmp_path / "deletions").glob("*.json"))


def test_delete_failure_keeps_project_and_journal(store, project, tmp_path, monkeypatch):
    client = app.test_client()
    _patch_delete_dependencies(monkeypatch, tmp_path)

    def fail_remove(project_id):
        raise RuntimeError("模拟 JSON 写入失败")

    monkeypatch.setattr(context_store, "remove_project", fail_remove)

    response = client.delete(f"/api/project/{project['id']}")
    body = response.get_json()

    assert response.status_code == 500
    assert body["code"] == "PROJECT_DELETE_FAILED"
    assert body["data"]["failed_step"] == "projects_json"
    assert store.get_project(project["id"]) is not None
    assert list((tmp_path / "deletions").glob("*.json"))


def test_put_project_keeps_pain_points(store, project):
    client = app.test_client()
    response = client.put(
        f"/api/project/{project['id']}",
        json={
            "client_name": "PUT客户",
            "industry": "行业",
            "production_mode": "MTO",
            "pain_points": "保留痛点",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["project"]["pain_points"] == "保留痛点"
