# -*- coding: utf-8 -*-
"""RAG 路由：对话（JSON/SSE）、上传、文档列表。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Generator

from flask import Blueprint, Response, jsonify, request, stream_with_context

from config import MAX_USER_INPUT_CHARS
from errors import ApiError
from routes.helpers import (
    json_object,
    parse_history,
    parse_stage,
    require_project_id,
    validate_message,
)
from services import chat_service, document_service, observability


rag_bp = Blueprint("rag_api", __name__, url_prefix="/api")
log = logging.getLogger("aps-workbench.rag-route")


def _parse_chat_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """统一解析 /api/chat 与 /api/chat/stream 的请求参数。"""
    project_id = require_project_id(payload)

    answer_mode = payload.get("answer_mode")
    if answer_mode is not None and answer_mode not in ("rag", "stage"):
        raise ApiError("INVALID_REQUEST", "answer_mode 只能是 rag 或 stage。", 400)

    message_value = payload.get("message")
    legacy_value = payload.get("user_input")
    correction = str(payload.get("correction", "")).strip()
    regenerate = bool(payload.get("regenerate", False))

    if answer_mode is None:
        answer_mode = "rag" if message_value is not None else "stage"

    chosen_message = message_value if message_value is not None else legacy_value
    message = validate_message(chosen_message, "message") if chosen_message else ""

    if regenerate:
        if answer_mode != "stage":
            raise ApiError(
                "INVALID_REQUEST",
                "只有 stage 模式支持重新生成。",
                400,
            )
    elif not message and not correction:
        raise ApiError(
            "VALIDATION_ERROR",
            "请输入 message，或提供 correction。",
            400,
        )
    if correction:
        if len(correction) > MAX_USER_INPUT_CHARS:
            raise ApiError(
                "MESSAGE_TOO_LONG",
                f"correction 最多 {MAX_USER_INPUT_CHARS} 字符。",
                400,
            )
        if answer_mode == "rag":
            raise ApiError(
                "INVALID_REQUEST",
                "rag 模式不支持 correction；纠偏重生成请使用 stage 模式。",
                400,
            )

    history = parse_history(payload.get("history"))
    stage = (
        parse_stage(payload.get("stage"))
        if payload.get("stage") not in (None, "")
        else None
    )
    return {
        "project_id": project_id,
        "message": message,
        "history": history,
        "stage": stage,
        "answer_mode": answer_mode,
        "correction": correction,
        "regenerate": regenerate,
    }


def _sse_message(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stage_sse_stream(params: Dict[str, Any]) -> Generator[str, None, None]:
    stream_error = False
    try:
        for item in chat_service.stream_chat(**params):
            if item["event"] == "error":
                stream_error = True
            yield _sse_message(item["event"], item["data"])
    except Exception:  # noqa: BLE001
        log.exception("阶段对话 SSE 异常")
        stream_error = True
        yield _sse_message(
            "error",
            {
                "ok": False,
                "code": "INTERNAL_ERROR",
                "message": "对话流式输出异常，请稍后重试。",
                "data": None,
                "error": "对话流式输出异常，请稍后重试。",
            },
        )
    finally:
        observability.log_request(200, stream=True, stream_error=stream_error)


@rag_bp.post("/chat")
def chat():
    """多轮 RAG / 阶段生成对话（非流式）。"""
    params = _parse_chat_payload(json_object())
    result: Dict[str, Any] = chat_service.handle_chat(**params)
    return jsonify({"ok": True, **result})


@rag_bp.post("/chat/stream")
def chat_stream():
    """多轮 RAG / 阶段生成对话（SSE）。"""
    params = _parse_chat_payload(json_object())
    observability.mark_streaming()
    return Response(
        stream_with_context(_stage_sse_stream(params)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@rag_bp.post("/upload")
def upload():
    """上传文档并同步建立向量索引。"""
    project_id = str(request.form.get("project_id", "")).strip()
    if not project_id:
        raise ApiError("VALIDATION_ERROR", "缺少 project_id。", 400)
    stage = parse_stage(request.form.get("stage"))
    file_storage = request.files.get("file")
    result = document_service.upload_document(
        project_id=project_id,
        stage=stage,
        file_storage=file_storage,
    )
    return jsonify({"ok": True, **result}), 201


@rag_bp.get("/documents/<project_id>")
def list_documents(project_id: str):
    result = document_service.list_documents(project_id)
    return jsonify({"ok": True, **result})
