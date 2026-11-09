# -*- coding: utf-8 -*-
"""全局 AI 小助手路由：对话、SSE 流与独立历史。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Generator

from flask import Blueprint, Response, jsonify, request, stream_with_context

from routes.helpers import json_object, require_project_id, validate_message
from services import assistant_service, observability


assistant_bp = Blueprint("assistant_api", __name__, url_prefix="/api/assistant")
log = logging.getLogger("aps-workbench.assistant-route")


def _sse_message(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_stream(project_id: str, message: str) -> Generator[str, None, None]:
    stream_error = False
    try:
        for item in assistant_service.stream_chat(project_id, message):
            if item["event"] == "error":
                stream_error = True
            yield _sse_message(item["event"], item["data"])
    except Exception:  # noqa: BLE001
        log.exception("助手 SSE 流异常")
        stream_error = True
        yield _sse_message(
            "error",
            {
                "ok": False,
                "code": "INTERNAL_ERROR",
                "message": "助手流式输出异常，请稍后重试。",
                "data": None,
                "error": "助手流式输出异常，请稍后重试。",
            },
        )
    finally:
        observability.log_request(200, stream=True, stream_error=stream_error)


@assistant_bp.post("/chat")
def assistant_chat():
    """全局助手问答；stream=true 或 Accept: text/event-stream 时返回 SSE。"""
    payload = json_object()
    project_id = require_project_id(payload)
    message = validate_message(payload.get("message"), "message")

    wants_stream = bool(payload.get("stream")) or (
        "text/event-stream" in request.headers.get("Accept", "")
    )
    if wants_stream:
        observability.mark_streaming()
        return Response(
            stream_with_context(_sse_stream(project_id, message)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = assistant_service.handle_chat(project_id, message)
    return jsonify({"ok": True, **result})


@assistant_bp.get("/history/<project_id>")
def assistant_history(project_id: str):
    messages = assistant_service.get_history(project_id)
    return jsonify({"ok": True, "messages": messages})
