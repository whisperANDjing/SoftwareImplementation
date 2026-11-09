# -*- coding: utf-8 -*-
"""左侧阶段 Agent 与右侧全局 Copilot 的职责边界测试。"""

from __future__ import annotations

from data import assistant_store
from services import assistant_service, retrieval_service
from services.stage_prompts import build_rag_system_prompt


def test_stage_agent_prompt_is_delivery_oriented(store, project):
    prompt = build_rag_system_prompt(project, 1, [], "stage")
    assert "你是负责【当前阶段】的专家" in prompt
    assert "输出必须是结构化文档格式，随时准备被归档" in prompt


def test_copilot_excludes_unarchived_draft_and_template(
    store, project, monkeypatch
):
    project_id = project["id"]
    store.append_exchange(
        project_id,
        1,
        {"role": "user", "content": "UNARCHIVED_STAGE_DRAFT"},
        {"role": "assistant", "content": "这是未归档草稿回复"},
    )
    store.set_initial_context(
        project_id,
        1,
        {
            "content": "UNARCHIVED_TEMPLATE_CONTEXT",
            "source_template_id": "old:1:1",
        },
    )
    monkeypatch.setattr(retrieval_service, "search", lambda pid, query: [])

    prepared = assistant_service._prepare(project_id, "这个项目已归档了什么？")
    prompt = prepared["system_prompt"]
    assert "全局知识助手" in prompt
    assert "快问快答" in prompt
    assert "不要主动生成长篇大论" in prompt
    assert "UNARCHIVED_STAGE_DRAFT" not in prompt
    assert "这是未归档草稿回复" not in prompt
    assert "UNARCHIVED_TEMPLATE_CONTEXT" not in prompt


def test_copilot_history_is_independent_and_short(store, project):
    project_id = project["id"]
    stage_before = store.get_project(project_id)["stages"]["1"][
        "conversation_history"
    ]
    for index in range(12):
        assistant_store.append_exchange(
            project_id,
            {"role": "user", "content": f"问题{index}"},
            {"role": "assistant", "content": f"回答{index}"},
        )
    messages = assistant_store.get_messages(project_id)
    assert len(messages) == 10
    assert messages[0]["content"] == "问题7"
    assert messages[-1]["content"] == "回答11"

    stage_after = store.get_project(project_id)["stages"]["1"][
        "conversation_history"
    ]
    assert len(stage_after) == len(stage_before)
    assert all("问题" not in item.get("content", "") for item in stage_after)
