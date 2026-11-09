# -*- coding: utf-8 -*-
"""首页仪表盘统计、排序与旧数据兼容测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

from services import dashboard_service


def _create(store, name):
    return store.create_project(
        {
            "client_name": name,
            "industry": "装备制造",
            "production_mode": "订单型 MTO",
            "pain_points": "插单",
        }
    )


def test_dashboard_stats_sorting_and_stage_mapping(store):
    now = datetime.now().astimezone()
    p1 = _create(store, "项目A")
    p2 = _create(store, "项目B")
    p3 = _create(store, "项目C")

    p1_state = store._projects[p1["id"]]
    p1_state["stages"]["1"]["status"] = "archived"
    p1_state["stages"]["2"]["status"] = "in_progress"
    p1_state["active_stage"] = 2
    p1_state["last_active_at"] = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    p1_state["last_summary"] = "客户当前按周排产，插单频繁，" + "很长" * 40

    p2_state = store._projects[p2["id"]]
    for stage_id in ("1", "2", "3", "4", "5"):
        p2_state["stages"][stage_id]["status"] = "archived"
    p2_state["last_active_at"] = (now - timedelta(days=10)).isoformat(timespec="seconds")

    p3_state = store._projects[p3["id"]]
    p3_state["created_at"] = (now - timedelta(days=2)).isoformat(timespec="seconds")
    p3_state.pop("last_active_at", None)
    p3_state.pop("last_summary", None)

    data = dashboard_service.get_dashboard()
    stats = data["stats"]

    assert stats["total_projects"] == 3
    assert stats["active_projects"] == 2
    assert stats["archived_stages"] == 6
    assert stats["active_last_7d"] == 2
    assert [item["project_id"] for item in data["projects"]] == [
        p1["id"],
        p3["id"],
        p2["id"],
    ]

    first = data["projects"][0]
    assert first["current_stage"] == "stage2_data"
    assert first["stage_status"]["stage1_research"] == "archived"
    assert first["stage_status"]["stage2_data"] == "in_progress"
    assert first["stage_status"]["stage5_golive"] == "not_started"
    assert len(first["last_summary"]) <= 51
    assert first["last_summary"].endswith("…")

    old = data["projects"][1]
    assert old["last_active_at"] == p3_state["created_at"]
    assert old["last_summary"] == ""


def test_normalise_project_adds_legacy_defaults(store):
    legacy = {
        "id": "prj_legacy",
        "client_name": "旧客户",
        "created_at": "2026-01-01T10:00:00+08:00",
        "active_stage": 1,
        "stages": {},
    }
    normalised = store._normalise_project(legacy)
    assert normalised["last_active_at"] == "2026-01-01T10:00:00+08:00"
    assert normalised["last_summary"] == ""
    assert len(normalised["stages"]) == 5
