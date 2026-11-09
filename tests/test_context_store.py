# -*- coding: utf-8 -*-
"""上下文读写、归档与阶段历史保留测试。"""

from __future__ import annotations


def test_project_context_read_write(store, project):
    project_id = project["id"]
    assert store.get_project(project_id)["client_name"] == "测试客户"

    updated = store.update_project_fields(
        project_id,
        {"industry": "汽车零部件", "pain_points": "插单与缺料"},
    )
    assert updated["industry"] == "汽车零部件"
    assert updated["pain_points"] == "插单与缺料"

    archived = store.archive_stage(
        project_id,
        1,
        "阶段一归档内容",
        last_summary="用户最近问题",
    )
    assert archived["stages"]["1"]["status"] == "archived"
    assert archived["stages"]["1"]["archive"]["version"] == 1
    assert archived["stages"]["1"]["archive"]["content"] == "阶段一归档内容"
    assert archived["last_active_at"]
    assert archived["last_summary"] == "用户最近问题"


def test_switch_stage_preserves_messages(store, project):
    project_id = project["id"]
    store.append_exchange(
        project_id,
        1,
        {"role": "user", "content": "阶段一问题"},
        {"role": "assistant", "content": "阶段一回答"},
    )
    store.set_active_stage(project_id, 2, reset_history=False)
    store.append_exchange(
        project_id,
        2,
        {"role": "user", "content": "阶段二问题"},
        {"role": "assistant", "content": "阶段二回答"},
    )
    store.set_active_stage(project_id, 1, reset_history=False)

    restored = store.get_project(project_id)
    assert restored["active_stage"] == 1
    assert len(restored["stages"]["1"]["conversation_history"]) == 2
    assert restored["stages"]["1"]["conversation_history"][0]["content"] == "阶段一问题"
    assert len(restored["stages"]["2"]["conversation_history"]) == 2


def test_client_supplied_history_replaces_stage_history(store, project):
    project_id = project["id"]
    store.append_exchange(
        project_id,
        1,
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
    )
    store.append_exchange(
        project_id,
        1,
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "新回答"},
        replace_history=[
            {"role": "user", "content": "客户端历史"},
            {"role": "assistant", "content": "客户端回答"},
        ],
    )

    messages = store.get_project(project_id)["stages"]["1"]["conversation_history"]
    assert len(messages) == 4
    assert messages[0]["content"] == "客户端历史"
    assert messages[-2]["content"] == "新问题"


def test_archive_service_uses_latest_user_question_as_summary(store, project):
    from services import project_service

    project_id = project["id"]
    store.append_exchange(
        project_id,
        1,
        {
            "role": "user",
            "content": "请总结插单影响",
            "display": "请总结插单影响",
        },
        {"role": "assistant", "content": "插单影响如下……"},
    )
    updated = project_service.archive_stage(
        project_id,
        1,
        "插单影响如下……",
        confirmations={"constraint_priority_confirmed": True},
    )
    assert updated["last_summary"] == "请总结插单影响"
    assert updated["last_active_at"]
