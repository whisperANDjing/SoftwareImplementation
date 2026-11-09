# -*- coding: utf-8 -*-
"""API 请求日志与请求 ID。

非流式请求在 after_request 记录；SSE 请求在生成器结束时记录，
从而包含完整的耗时和 token 消耗。
"""

from __future__ import annotations

import logging
import time
import uuid

from flask import g, request

from services import metrics


log = logging.getLogger("aps-workbench.api")


def start_request() -> None:
    """注册请求开始时间、请求 ID，并重置 token 统计。"""
    g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    g.started_at = time.perf_counter()
    g.api_logged = False
    g.streaming_response = False
    metrics.reset()


def mark_streaming() -> None:
    """标记当前响应为 SSE；after_request 不重复记录。"""
    g.streaming_response = True


def log_request(
    status_code: int = 200,
    *,
    stream: bool = False,
    stream_error: bool = False,
) -> None:
    """记录一次 API 请求；同一请求只记录一次。"""
    if not request.path.startswith("/api/"):
        return
    if getattr(g, "api_logged", False):
        return
    usage = metrics.snapshot()
    duration_ms = (time.perf_counter() - getattr(g, "started_at", time.perf_counter())) * 1000
    log.info(
        "api_request method=%s path=%s status=%s duration_ms=%.1f "
        "request_id=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s "
        "tokens_estimated=%s stream=%s stream_error=%s",
        request.method,
        request.path,
        status_code,
        duration_ms,
        getattr(g, "request_id", "-"),
        usage["prompt_tokens"],
        usage["completion_tokens"],
        usage["total_tokens"],
        usage["estimated"],
        stream,
        stream_error,
    )
    g.api_logged = True
