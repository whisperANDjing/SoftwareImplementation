# -*- coding: utf-8 -*-
"""DeepSeek LLM 调用服务。

只封装外部调用与错误映射，不负责提示词拼装、RAG 或业务降级。
支持一次性返回和 SSE 友好的 token 流。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Generator, List

from config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from errors import LlmCallError, LlmNotConfigured
from services import metrics


log = logging.getLogger("aps-workbench.llm")

try:
    from openai import OpenAI
    from openai import APITimeoutError, AuthenticationError, RateLimitError
except ImportError:  # pragma: no cover - 依赖缺失时
    OpenAI = None  # type: ignore[assignment]
    APITimeoutError = None  # type: ignore[assignment]
    AuthenticationError = None  # type: ignore[assignment]
    RateLimitError = None  # type: ignore[assignment]


def has_api_key() -> bool:
    """运行期读取环境变量。"""
    return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


def mode() -> str:
    """返回前端展示模式。"""
    return "deepseek" if has_api_key() else "demo"


def _build_messages(
    system_prompt: str,
    history: List[Dict[str, Any]],
    user_message: str,
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for item in history:
        role = item.get("role")
        content = str(item.get("content", ""))
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


def _client():
    if not has_api_key():
        raise LlmNotConfigured("未配置 DEEPSEEK_API_KEY")
    if OpenAI is None:
        raise LlmCallError(
            "LLM_UPSTREAM_ERROR",
            "未安装 openai 库，请运行 pip install -r requirements.txt。",
        )
    return OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        base_url=DEEPSEEK_BASE_URL,
        timeout=75.0,
        max_retries=1,
    )


def _raise_mapped(exc: Exception) -> None:
    """把 SDK 异常映射为稳定业务错误码。"""
    if AuthenticationError is not None and isinstance(exc, AuthenticationError):
        raise LlmCallError(
            "LLM_AUTH_FAILED",
            "DeepSeek 鉴权失败，请检查 DEEPSEEK_API_KEY 是否有效。",
        ) from exc
    if APITimeoutError is not None and isinstance(exc, APITimeoutError):
        raise LlmCallError(
            "LLM_TIMEOUT",
            "DeepSeek 调用超时，请稍后重试。",
            504,
        ) from exc
    if RateLimitError is not None and isinstance(exc, RateLimitError):
        raise LlmCallError(
            "LLM_RATE_LIMITED",
            "DeepSeek 请求过于频繁，请稍后重试。",
            429,
        ) from exc
    raise LlmCallError(
        "LLM_UPSTREAM_ERROR",
        f"DeepSeek 调用失败：{type(exc).__name__}。请稍后重试。",
    ) from exc


def complete(
    system_prompt: str,
    history: List[Dict[str, Any]],
    user_message: str,
    *,
    max_tokens: int = 2_600,
    temperature: float = 0.3,
) -> str:
    """调用 DeepSeek 生成完整回答。

    输入：系统提示词、历史消息、本轮用户消息
    输出：回答文本
    异常：LlmNotConfigured / LlmCallError
    """
    client = _client()
    messages = _build_messages(system_prompt, history, user_message)
    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise LlmCallError(
                "LLM_UPSTREAM_ERROR",
                "DeepSeek 返回了空内容，请稍后重试。",
            )
        usage = getattr(response, "usage", None)
        if usage:
            metrics.record_usage(usage)
        else:
            prompt_chars = len(system_prompt) + sum(
                len(str(item.get("content", ""))) for item in history
            ) + len(user_message)
            metrics.record_estimate(prompt_chars, len(content))
        return content
    except LlmCallError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("DeepSeek 调用失败")
        _raise_mapped(exc)


def stream_complete(
    system_prompt: str,
    history: List[Dict[str, Any]],
    user_message: str,
    *,
    max_tokens: int = 2_600,
    temperature: float = 0.3,
) -> Generator[str, None, None]:
    """流式调用 DeepSeek，逐 token 产出文本。

    输入：系统提示词、历史消息、本轮用户消息
    输出：文本片段生成器
    异常：LlmNotConfigured / LlmCallError
    """
    client = _client()
    messages = _build_messages(system_prompt, history, user_message)
    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        produced = False
        produced_chars = 0
        saw_usage = False
        for chunk in response:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                metrics.record_usage(chunk_usage)
                saw_usage = True
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta else None
            if content:
                produced = True
                produced_chars += len(content)
                yield content
        if not produced:
            raise LlmCallError(
                "LLM_UPSTREAM_ERROR",
                "DeepSeek 没有返回有效内容，请稍后重试。",
            )
        if not saw_usage:
            prompt_chars = len(system_prompt) + sum(
                len(str(item.get("content", ""))) for item in history
            ) + len(user_message)
            metrics.record_estimate(prompt_chars, produced_chars)
    except LlmCallError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("DeepSeek 流式调用失败")
        _raise_mapped(exc)
