# -*- coding: utf-8 -*-
"""首页仪表盘服务：从项目上下文实时计算统计和项目卡片数据。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from data import context_store
from services.stage_prompts import STAGE_API_KEYS, STAGES
from services.text_utils import summarize_text


def _parse_datetime(value: Any) -> Optional[datetime]:
    """解析 ISO 时间并统一为带时区 datetime；非法/空值返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _last_active(project: Dict[str, Any]) -> str:
    """旧项目缺失 last_active_at 时回退到 created_at。"""
    return str(
        project.get("last_active_at")
        or project.get("created_at")
        or ""
    ).strip()


def _stage_status(project: Dict[str, Any]) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    stages = project.get("stages", {}) or {}
    for stage in STAGES:
        stage_id = stage["id"]
        state = stages.get(str(stage_id), {}) or {}
        status = state.get("status", "not_started")
        statuses[STAGE_API_KEYS[stage_id]] = status
    return statuses


def _current_stage(project: Dict[str, Any]) -> str:
    try:
        stage_id = int(project.get("active_stage", 1))
    except (TypeError, ValueError):
        stage_id = 1
    if stage_id not in STAGE_API_KEYS:
        stage_id = 1
    return STAGE_API_KEYS[stage_id]


def _project_card(project: Dict[str, Any]) -> Tuple[datetime, Dict[str, Any]]:
    last_active_at = _last_active(project)
    parsed = _parse_datetime(last_active_at)
    sort_key = parsed or datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo)
    stage_status = _stage_status(project)
    card = {
        "project_id": project.get("id", ""),
        "client_name": project.get("client_name", ""),
        "industry": project.get("industry", ""),
        "production_mode": project.get("production_mode", ""),
        "current_stage": _current_stage(project),
        "stage_status": stage_status,
        "last_active_at": last_active_at,
        "last_summary": summarize_text(project.get("last_summary", ""), 50),
        "created_at": str(project.get("created_at", "") or ""),
    }
    return sort_key, card


def get_dashboard() -> Dict[str, Any]:
    """返回实时统计与按 last_active_at 倒序的项目卡片。"""
    projects = context_store.list_all_projects()
    now = datetime.now().astimezone()
    cutoff = now - timedelta(days=7)

    archived_stages = 0
    active_projects = 0
    active_last_7d = 0
    cards: List[Tuple[datetime, Dict[str, Any]]] = []

    for project in projects:
        statuses = _stage_status(project)
        archived_count = sum(1 for status in statuses.values() if status == "archived")
        archived_stages += archived_count
        if any(status != "archived" for status in statuses.values()):
            active_projects += 1

        last_active_at = _last_active(project)
        parsed = _parse_datetime(last_active_at)
        if parsed and parsed >= cutoff:
            active_last_7d += 1

        cards.append(_project_card(project))

    cards.sort(key=lambda item: item[0], reverse=True)
    return {
        "stats": {
            "total_projects": len(projects),
            "active_projects": active_projects,
            "archived_stages": archived_stages,
            "active_last_7d": active_last_7d,
        },
        "projects": [card for _, card in cards],
    }


def project_summary(project: Dict[str, Any]) -> Dict[str, Any]:
    """把单个项目转换为与仪表盘一致的项目卡片。"""
    return _project_card(project)[1]
