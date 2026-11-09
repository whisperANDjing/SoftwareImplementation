# -*- coding: utf-8 -*-
"""上线后跟踪：问题列表、Agent 分析与 Excel 导出。"""

from __future__ import annotations

from typing import Any, Dict, List

from data import context_store
from errors import ApiError, LlmCallError, LlmNotConfigured
from services import export_service, llm_service


SEVERITIES = {"high", "medium", "low"}
STATUSES = {"pending", "in_progress", "resolved"}


def _project(project_id: str) -> Dict[str, Any]:
    project = context_store.get_project(project_id)
    if not project or project.get("record_type", "delivery") != "delivery":
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    return project


def _unlocked(project: Dict[str, Any]) -> bool:
    post_launch = project.get("post_launch") or {}
    if post_launch.get("unlocked_at"):
        return True
    return (project.get("stages", {}).get("5", {}) or {}).get("status") == "archived"


def _ensure_unlocked(project: Dict[str, Any]) -> None:
    if not _unlocked(project):
        raise ApiError(
            "POST_LAUNCH_LOCKED",
            "阶段五归档后才能使用上线跟踪。",
            409,
        )


def _validate_issue(payload: Dict[str, Any], partial: bool = False) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    if not partial or "description" in payload:
        description = str(payload.get("description", "")).strip()
        if not description:
            raise ApiError("ISSUE_VALIDATION_ERROR", "问题描述不能为空。", 400)
        if len(description) > 2000:
            raise ApiError("ISSUE_VALIDATION_ERROR", "问题描述不能超过 2000 字符。", 400)
        fields["description"] = description
    if not partial or "severity" in payload:
        severity = str(payload.get("severity", "medium")).strip()
        if severity not in SEVERITIES:
            raise ApiError("ISSUE_VALIDATION_ERROR", "严重程度必须是 high/medium/low。", 400)
        fields["severity"] = severity
    if not partial or "status" in payload:
        status = str(payload.get("status", "pending")).strip()
        if status not in STATUSES:
            raise ApiError("ISSUE_VALIDATION_ERROR", "状态必须是 pending/in_progress/resolved。", 400)
        fields["status"] = status
    return fields


def get_issues(project_id: str) -> Dict[str, Any]:
    project = _project(project_id)
    if _unlocked(project):
        context_store.ensure_post_launch_unlocked(project_id)
    data = context_store.list_issues(project_id)
    if not data:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    return data


def add_issue(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    project = _project(project_id)
    _ensure_unlocked(project)
    fields = _validate_issue(payload)
    issue = context_store.add_issue(
        project_id,
        fields["description"],
        fields["severity"],
        fields["status"],
    )
    if not issue:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    return issue


def update_issue(project_id: str, issue_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    project = _project(project_id)
    _ensure_unlocked(project)
    fields = _validate_issue(payload, partial=True)
    if not fields:
        raise ApiError("ISSUE_VALIDATION_ERROR", "至少提供一个修改字段。", 400)
    issue = context_store.update_issue(project_id, issue_id, fields)
    if not issue:
        raise ApiError("ISSUE_NOT_FOUND", "上线问题不存在。", 404)
    return issue


def _fallback(issues: List[Dict[str, Any]], prompt: str) -> str:
    high = [item for item in issues if item.get("severity") == "high"]
    open_items = [item for item in issues if item.get("status") != "resolved"]
    lines = [
        "# 上线问题分析（本地演示）",
        "",
        f"- 问题总数：{len(issues)}",
        f"- 高严重问题：{len(high)}",
        f"- 未解决问题：{len(open_items)}",
        "",
        "## 优先关注",
    ]
    for item in (high or open_items)[:5]:
        lines.append(f"- {item.get('description', '')}")
    if "需求池" in prompt:
        lines.extend(
            [
                "",
                "## 本次迭代需求池建议",
                "- 优先处理高严重且影响计划稳定性的插单/缺料问题。",
                "- 将反复出现的问题沉淀为规则或校验项，进入下一轮配置。",
                "- 对需要客户确认的规则，补充验收标准和责任人。",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## 排查建议",
                "- 先复现问题并记录订单、物料、设备和时间窗口。",
                "- 对照排程约束检查是否存在未建模的换型、替代或冻结规则。",
                "- 对高频问题建立监控指标，并约定闭环责任人。",
            ]
        )
    lines.append("\n> 当前为本地演示输出，配置 DeepSeek Key 后可生成更完整的分析。")
    return "\n".join(lines)


def analyze_issues(project_id: str, prompt: str) -> Dict[str, Any]:
    project = _project(project_id)
    _ensure_unlocked(project)
    data = context_store.list_issues(project_id) or {"issues": []}
    issues = data.get("issues", [])
    prompt = str(prompt or "").strip() or "基于当前问题记录，生成本次迭代的需求池摘要"
    if not issues:
        return {
            "reply": "当前没有问题记录。请先添加客户反馈的问题，再生成分析。",
            "mode": "no_data",
        }
    system_prompt = """你是 APS 上线后的运营顾问。请只基于给出的问题列表进行归纳，
不得虚构客户事实、问题数量或系统能力。

输出 Markdown：
- 若用户要求“需求池摘要”：按主题分组，给出优先级、建议动作和验收口径。
- 若用户要求“排查建议”：按高频/高严重问题给出排查路径、需要的数据和责任人建议。
- 明确区分【事实】与【建议】。"""
    issue_text = "\n".join(
        f"- [{item.get('severity')}/{item.get('status')}] {item.get('description')}"
        for item in issues
    )
    try:
        reply = llm_service.complete(
            system_prompt=system_prompt,
            history=[],
            user_message=f"用户要求：{prompt}\n\n当前问题：\n{issue_text}",
        )
        return {"reply": reply, "mode": "deepseek"}
    except LlmNotConfigured:
        return {"reply": _fallback(issues, prompt), "mode": "demo"}
    except LlmCallError:
        return {"reply": _fallback(issues, prompt), "mode": "demo-fallback"}
    except Exception:
        return {"reply": _fallback(issues, prompt), "mode": "demo-fallback"}


def export_excel(project_id: str) -> export_service.ExportFile:
    project = _project(project_id)
    _ensure_unlocked(project)
    data = context_store.list_issues(project_id) or {"issues": []}
    return export_service.build_issues_excel(
        project.get("client_name", "未命名客户"),
        data.get("issues", []),
    )
