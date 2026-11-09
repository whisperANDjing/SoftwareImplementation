# -*- coding: utf-8 -*-
"""对话历史迁移、恢复、重新生成与清空测试。"""

from __future__ import annotations

from app import app
from data import context_store
from services import chat_service, llm_service, project_service, retrieval_service


def test_legacy_messages_migrate_to_conversation_history(store):
    legacy = {
        "id": "prj_legacy",
        "client_name": "旧项目",
        "created_at": "2026-01-01T10:00:00+08:00",
        "stages": {
            "1": {
                "status": "in_progress",
                "messages": [
                    {
                        "role": "user",
                        "content": "内部 prompt",
                        "display": "旧问题",
                        "ts": "2026-01-01T10:01:00+08:00",
                    },
                    {
                        "role": "assistant",
                        "content": "旧回答",
                        "ts": "2026-01-01T10:01:01+08:00",
                        "sources": [],
                    },
                ],
            }
        },
    }
    normalised = store._normalise_project(legacy)
    history = normalised["stages"]["1"]["conversation_history"]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "旧问题"
    assert history[0]["timestamp"] == "2026-01-01T10:01:00+08:00"
    assert "messages" not in normalised["stages"]["1"]
    assert len(normalised["stages"]) == 5


def test_context_returns_history_and_messages_alias(store, project):
    store.append_exchange(
        project["id"],
        1,
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答", "sources": []},
    )
    data = project_service.get_context(project["id"])
    stage = data["project"]["stages"]["1"]
    assert len(stage["conversation_history"]) == 2
    assert len(stage["messages"]) == 2
    assert stage["messages"][0]["display"] == "问题"
    assert stage["conversation_history"][1]["sources"] == []


def test_clear_conversation_keeps_archive(store, project):
    project_id = project["id"]
    store.append_exchange(
        project_id,
        1,
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    )
    store.archive_stage(project_id, 1, "归档内容", last_summary="问题")
    updated = project_service.clear_conversation(project_id, 1)
    stage = updated["stages"]["1"]
    assert stage["conversation_history"] == []
    assert stage["archive"]["content"] == "归档内容"
    assert stage["status"] == "archived"


def test_clear_conversation_resets_empty_stage_status(store, project):
    project_id = project["id"]
    store.append_exchange(
        project_id,
        1,
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    )
    updated = project_service.clear_conversation(project_id, 1)
    assert updated["stages"]["1"]["conversation_history"] == []
    assert updated["stages"]["1"]["status"] == "not_started"


def test_clear_conversation_route(store, project):
    store.append_exchange(
        project["id"],
        1,
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    )
    response = app.test_client().post(
        "/api/conversation/clear",
        json={"project_id": project["id"], "stage": 1},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["conversation_history"] == []
    assert body["project"]["stages"]["1"]["conversation_history"] == []


def test_regenerate_replaces_last_assistant_without_duplicate(store, project, monkeypatch):
    monkeypatch.setattr(retrieval_service, "search", lambda pid, query: [])
    monkeypatch.setattr(llm_service, "has_api_key", lambda: True)
    replies = iter(["第一次回答", "第二次回答"])
    monkeypatch.setattr(
        llm_service,
        "complete",
        lambda system_prompt, history, user_message, **kwargs: next(replies),
    )

    chat_service.handle_chat(
        project_id=project["id"],
        message="原始问题",
        history=None,
        stage=1,
        answer_mode="stage",
    )
    result = chat_service.handle_chat(
        project_id=project["id"],
        message="应被忽略的重复问题",
        history=None,
        stage=1,
        answer_mode="stage",
        regenerate=True,
    )

    history = context_store.get_project(project["id"])["stages"]["1"][
        "conversation_history"
    ]
    assert len(history) == 2
    assert history[0]["content"] == "原始问题"
    assert history[1]["content"] == "第二次回答"
    assert result["regenerated"] is True
