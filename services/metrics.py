# -*- coding: utf-8 -*-
"""请求级 LLM token 用量统计。

使用 ContextVar 隔离并发请求；流式响应的统计在生成器结束时读取。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, Optional


_usage: ContextVar[Optional[Dict[str, Any]]] = ContextVar("aps_llm_usage", default=None)


def reset() -> None:
    """每个请求开始时调用。"""
    _usage.set(
        {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "estimated": False,
        }
    )


def _current() -> Dict[str, Any]:
    value = _usage.get()
    if value is None:
        reset()
        value = _usage.get()
    return value  # type: ignore[return-value]


def record_usage(usage: Any) -> None:
    """记录 OpenAI/DeepSeek 返回的 usage 对象。"""
    if not usage:
        return
    data = _current()
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or (prompt + completion))
    data["prompt_tokens"] += prompt
    data["completion_tokens"] += completion
    data["total_tokens"] += total
    data["calls"] += 1


def record_estimate(prompt_chars: int, completion_chars: int) -> None:
    """无 usage 时按字符数估算，日志会标记 estimated。"""
    data = _current()
    # 中文场景下粗略按 2 字符 ≈ 1 token，仅用于可观测性，不参与计费。
    prompt = max(1, int(prompt_chars / 2))
    completion = max(1, int(completion_chars / 2))
    data["prompt_tokens"] += prompt
    data["completion_tokens"] += completion
    data["total_tokens"] += prompt + completion
    data["calls"] += 1
    data["estimated"] = True


def snapshot() -> Dict[str, Any]:
    return dict(_current())
