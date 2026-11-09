# -*- coding: utf-8 -*-
"""路由层通用参数校验。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from flask import request

from config import MAX_HISTORY_CHARS, MAX_HISTORY_MESSAGES, MAX_USER_INPUT_CHARS, STAGE_IDS
from errors import ApiError


def json_object() -> Dict[str, Any]:
    """读取 JSON 请求体，并要求顶层为对象。"""
    if not request.is_json:
        raise ApiError("INVALID_JSON", "请求体必须是 application/json。", 400)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("INVALID_JSON", "请求体必须是 JSON 对象。", 400)
    return payload


def require_project_id(payload: Dict[str, Any]) -> str:
    project_id = str(payload.get("project_id", "")).strip()
    if not project_id:
        raise ApiError("VALIDATION_ERROR", "缺少 project_id。", 400)
    return project_id


def parse_stage(value: Any, *, default: Optional[int] = None) -> int:
    """把输入统一成 1..5 的阶段整数。"""
    if value is None or value == "":
        if default is None:
            raise ApiError("VALIDATION_ERROR", "缺少 stage。", 400)
        return default
    try:
        stage = int(value)
    except (TypeError, ValueError):
        raise ApiError("INVALID_STAGE", "阶段参数必须是 1-5。", 400) from None
    if stage not in STAGE_IDS:
        raise ApiError("INVALID_STAGE", "阶段参数必须是 1-5。", 400)
    return stage


def parse_history(value: Any) -> Optional[list[dict[str, str]]]:
    """校验并规范化 history；None 表示请求未提供。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ApiError("HISTORY_INVALID", "history 必须是数组。", 400)
    if len(value) > MAX_HISTORY_MESSAGES:
        raise ApiError(
            "HISTORY_TOO_LONG",
            f"history 最多 {MAX_HISTORY_MESSAGES} 条。",
            400,
        )

    cleaned: list[dict[str, str]] = []
    total_chars = 0
    for item in value:
        if not isinstance(item, dict):
            raise ApiError("HISTORY_INVALID", "history 每条必须是对象。", 400)
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role not in ("user", "assistant"):
            raise ApiError(
                "HISTORY_INVALID",
                "history.role 只能是 user 或 assistant。",
                400,
            )
        if not content:
            raise ApiError("HISTORY_INVALID", "history.content 不能为空。", 400)
        if len(content) > MAX_USER_INPUT_CHARS:
            raise ApiError(
                "MESSAGE_TOO_LONG",
                f"history 单条消息最多 {MAX_USER_INPUT_CHARS} 字符。",
                400,
            )
        total_chars += len(content)
        cleaned.append({"role": role, "content": content})

    if total_chars > MAX_HISTORY_CHARS:
        raise ApiError(
            "HISTORY_TOO_LONG",
            f"history 总字符数最多 {MAX_HISTORY_CHARS}。",
            400,
        )
    return cleaned


def validate_message(value: Any, field: str = "message") -> str:
    text = str(value or "").strip()
    if not text:
        raise ApiError("VALIDATION_ERROR", f"{field} 不能为空。", 400)
    if len(text) > MAX_USER_INPUT_CHARS:
        raise ApiError(
            "MESSAGE_TOO_LONG",
            f"{field} 最多 {MAX_USER_INPUT_CHARS} 字符。",
            400,
        )
    return text
