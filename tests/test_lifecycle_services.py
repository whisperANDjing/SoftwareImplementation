# -*- coding: utf-8 -*-
"""售前、上线跟踪、门禁、模板复用测试。"""

from __future__ import annotations

from io import BytesIO

from docx import Document
from openpyxl import load_workbook

from app import app
from data import context_store
from services import chat_service, gate_service, llm_service, retrieval_service


def test_presales_create_list_page_and_export(store, monkeypatch):
    monkeypatch.setattr(llm_service, "has_api_key", lambda: False)
    client = app.test_client()
    response = client.post(
        "/api/presales",
        json={
            "industry": "汽车零部件",
            "production_mode": "订单型 MTO",
            "pain_points": "插单频繁，缺料发现晚",
            "scale": "3 个工厂 / 1200 人",
        },
    )
    body = response.get_json()
    assert response.status_code == 201
    assert body["presales_id"].startswith("presales_")
    assert "APS 价值点初稿" in body["assessment"]["content"]

    listed = client.get("/api/presales").get_json()
    assert listed["presales"][0]["presales_id"] == body["presales_id"]
    assert client.get(f"/presales/{body['presales_id']}").status_code == 200

    dashboard = client.get("/api/dashboard").get_json()
    assert dashboard["stats"]["total_projects"] == 0

    exported = client.post(
        f"/api/presales/{body['presales_id']}/export/word"
    )
    assert exported.status_code == 200
    assert exported.data.startswith(b"PK")
    document = Document(BytesIO(exported.data))
    assert "APS 价值点初稿" in "\n".join(p.text for p in document.paragraphs)


def test_post_launch_locked_and_unlocked_issue_flow(store, project, monkeypatch):
    client = app.test_client()
    locked = client.get(f"/api/issues/{project['id']}").get_json()
    assert locked["unlocked"] is False
    blocked = client.post(
        f"/api/issues/{project['id']}",
        json={"description": "锁定问题", "severity": "high"},
    )
    assert blocked.status_code == 409

    store.archive_stage(project["id"], 5, "上线验收完成")
    unlocked = client.get(f"/api/issues/{project['id']}").get_json()
    assert unlocked["unlocked"] is True

    created = client.post(
        f"/api/issues/{project['id']}",
        json={
            "description": "客户反馈插单后重排时间过长",
            "severity": "high",
            "status": "pending",
        },
    )
    issue = created.get_json()["issue"]
    assert created.status_code == 201
    updated = client.patch(
        f"/api/issues/{project['id']}/{issue['issue_id']}",
        json={"status": "in_progress"},
    )
    assert updated.get_json()["issue"]["status"] == "in_progress"

    monkeypatch.setattr(llm_service, "has_api_key", lambda: False)
    analysis = client.post(
        "/api/chat/issues",
        json={
            "project_id": project["id"],
            "prompt": "基于当前问题记录，生成本次迭代的需求池摘要",
        },
    )
    assert analysis.status_code == 200
    assert "需求池" in analysis.get_json()["reply"]

    exported = client.post(
        "/api/export/issues",
        json={"project_id": project["id"]},
    )
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.data))
    sheet = workbook.active
    assert sheet.cell(1, 1).value == "问题描述"
    assert sheet.freeze_panes == "A2"


def test_archive_gates_and_stage5_checklist(store, project):
    from errors import ApiError
    from services import project_service

    try:
        project_service.archive_stage(project["id"], 1, "阶段一内容")
    except ApiError as exc:
        assert exc.code == "GATE_CONFIRMATION_REQUIRED"
    else:
        raise AssertionError("阶段一未确认不应归档")

    updated = project_service.archive_stage(
        project["id"],
        1,
        "阶段一内容",
        confirmations={"constraint_priority_confirmed": True},
    )
    gates = updated["stages"]["1"]["gate_confirmations"]
    assert gates[-1]["gate"] == "constraint_priority"
    assert gates[-1]["confirmed_at"]

    try:
        project_service.archive_stage(project["id"], 3, "阶段三规则")
    except ApiError as exc:
        assert exc.code == "GATE_CONFIRMATION_REQUIRED"
    else:
        raise AssertionError("阶段三未确认人不应归档")

    updated = project_service.archive_stage(
        project["id"],
        3,
        "阶段三规则",
        confirmations={"confirmer_and_date": "张伟 / 2026-11-09"},
    )
    assert updated["stages"]["3"]["gate_confirmations"][-1]["payload"][
        "confirmer_and_date"
    ] == "张伟 / 2026-11-09"

    content = gate_service.ensure_stage5_checklist("## 上线检查清单\n- 数据准备")
    assert gate_service.STAGE5_CHECKLIST_ITEM in content


def test_template_list_and_injection(store, project):
    store.archive_stage(project["id"], 1, "排产约束优先级：交期优先")
    client = app.test_client()
    templates = client.get("/api/templates").get_json()["templates"]
    assert templates
    template_id = templates[0]["template_id"]

    created = client.post(
        "/api/project",
        json={
            "client_name": "模板新客户",
            "industry": "装备制造",
            "production_mode": "MTO",
            "template_id": template_id,
        },
    )
    body = created.get_json()
    assert created.status_code == 201
    new_id = body["project_id"]
    context = client.get(f"/api/context/{new_id}").get_json()
    initial = context["project"]["stages"]["1"]["initial_context"]
    assert initial["content"] == "排产约束优先级：交期优先"
    assert context["project"]["stages"]["1"]["status"] == "not_started"


def test_stage5_chat_appends_mandatory_checklist(store, project, monkeypatch):
    monkeypatch.setattr(retrieval_service, "search", lambda pid, query: [])
    monkeypatch.setattr(llm_service, "has_api_key", lambda: False)
    result = chat_service.handle_chat(
        project_id=project["id"],
        message="请生成上线检查清单",
        history=None,
        stage=5,
        answer_mode="stage",
    )
    assert gate_service.STAGE5_CHECKLIST_ITEM in result["reply"]
