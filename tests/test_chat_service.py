# -*- coding: utf-8 -*-
"""对话服务测试：命中、未命中、多轮历史与 SSE 事件。"""

from __future__ import annotations

from data import context_store
from services import chat_service, llm_service, retrieval_service


SOURCE = {
    "file": "排产计划.xlsx",
    "snippet": "排产周期 7 天；计划冻结期 1 天。",
    "doc_id": "doc_test",
    "stage": 2,
    "score": 0.78,
    "sheet": "排产参数",
    "row_start": 2,
    "row_end": 3,
}


def test_rag_hit_returns_sources_and_persists(monkeypatch, project):
    monkeypatch.setattr(retrieval_service, "search", lambda pid, query: [SOURCE])
    monkeypatch.setattr(llm_service, "has_api_key", lambda: False)

    result = chat_service.handle_chat(
        project_id=project["id"],
        message="这个文件里排产周期是多少？",
        history=None,
        stage=2,
        answer_mode="rag",
    )

    assert result["grounded"] is True
    assert result["sources"] == [SOURCE]
    assert result["mode"] == "demo"
    assert "7 天" in result["reply"]
    stored = context_store.get_project(project["id"])
    messages = stored["stages"]["2"]["conversation_history"]
    assert len(messages) == 2
    assert messages[1]["sources"][0]["file"] == "排产计划.xlsx"
    assert stored["last_active_at"]
    assert stored["last_summary"].startswith("这个文件里排产周期")


def test_rag_no_hit_returns_fixed_fallback_without_llm(monkeypatch, project):
    monkeypatch.setattr(retrieval_service, "search", lambda pid, query: [])
    monkeypatch.setattr(llm_service, "has_api_key", lambda: False)
    called = {"value": False}

    def fail_if_called(*args, **kwargs):
        called["value"] = True
        return "should not be called"

    monkeypatch.setattr(llm_service, "complete", fail_if_called)

    result = chat_service.handle_chat(
        project_id=project["id"],
        message="这个文件里食堂菜单是什么？",
        history=None,
        stage=2,
        answer_mode="rag",
    )

    assert result["mode"] == "no_context"
    assert result["sources"] == []
    assert result["reply"].startswith("当前知识库中没有相关信息")
    assert called["value"] is False


def test_multi_turn_history_is_passed_to_llm(monkeypatch, project):
    monkeypatch.setattr(retrieval_service, "search", lambda pid, query: [])
    monkeypatch.setattr(llm_service, "has_api_key", lambda: True)
    captured = []

    def fake_complete(system_prompt, history, user_message, **kwargs):
        captured.append(list(history))
        return f"已收到：{user_message[:20]}"

    monkeypatch.setattr(llm_service, "complete", fake_complete)

    for text in ["第一个约束是模具寿命", "第二个约束是人员技能", "刚才说的那个约束是什么？"]:
        chat_service.handle_chat(
            project_id=project["id"],
            message=text,
            history=None,
            stage=1,
            answer_mode="stage",
        )

    third_history = captured[2]
    assert any("模具寿命" in item["content"] for item in third_history)
    assert any("人员技能" in item["content"] for item in third_history)
    assert len(third_history) == 4


def test_stage_stream_emits_events_and_persists(monkeypatch, project):
    monkeypatch.setattr(retrieval_service, "search", lambda pid, query: [])
    monkeypatch.setattr(llm_service, "has_api_key", lambda: False)

    events = list(
        chat_service.stream_chat(
            project_id=project["id"],
            message="请生成本阶段诊断",
            history=None,
            stage=1,
            answer_mode="stage",
        )
    )
    names = [event["event"] for event in events]

    assert "meta" in names
    assert "sources" in names
    assert "delta" in names
    assert "done" in names
    stored = context_store.get_project(project["id"])
    assert len(stored["stages"]["1"]["conversation_history"]) == 2
