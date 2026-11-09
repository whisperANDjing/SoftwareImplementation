# -*- coding: utf-8 -*-
"""对话服务：编排检索、提示词、DeepSeek、历史持久化、来源与流式输出。"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Generator, List, Optional

from config import MAX_HISTORY_MESSAGES
from data import context_store
from errors import ApiError, LlmCallError, LlmNotConfigured
from services import gate_service, llm_service, project_deletion_service, retrieval_service
from services.demo_service import (
    build_extractive_rag_reply,
    build_stage_demo_reply,
)
from services.stage_prompts import NO_CONTEXT_REPLY, build_rag_system_prompt
from services.text_utils import summarize_text


log = logging.getLogger("aps-workbench.chat")


def _normalise_history(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """只保留 LLM 需要的 role/content，并截取最近若干条。"""
    cleaned: List[Dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content", "")).strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})
    return cleaned[-MAX_HISTORY_MESSAGES:]


def _prepare_chat(
    project_id: str,
    message: str,
    history: Optional[List[Dict[str, Any]]],
    stage: Optional[int],
    answer_mode: str,
    correction: str = "",
    regenerate: bool = False,
) -> Dict[str, Any]:
    """校验项目/阶段、加载历史、检索并构建系统提示词。"""
    project = context_store.get_project(project_id)
    if not project:
        raise ApiError(
            "PROJECT_NOT_FOUND",
            "项目不存在，请刷新页面或重新创建项目。",
            404,
        )
    project_deletion_service.ensure_not_deleting(project_id)
    if stage is None:
        stage = int(project.get("active_stage", 1))
    if str(stage) not in project["stages"]:
        raise ApiError("INVALID_STAGE", "阶段参数必须是 1-5。", 400)
    if answer_mode not in ("rag", "stage"):
        raise ApiError("INVALID_REQUEST", "answer_mode 只能是 rag 或 stage。", 400)

    stage_state = project["stages"][str(stage)]
    stored_history = list(stage_state.get("conversation_history", []))
    regenerate_user = None
    if regenerate:
        if not stored_history or stored_history[-1].get("role") != "assistant":
            raise ApiError(
                "NO_ASSISTANT_MESSAGE",
                "当前阶段没有可重新生成的 Agent 回答。",
                400,
            )
        last_user_index = None
        for index in range(len(stored_history) - 2, -1, -1):
            if stored_history[index].get("role") == "user":
                last_user_index = index
                break
        if last_user_index is None:
            raise ApiError(
                "NO_ASSISTANT_MESSAGE",
                "当前阶段没有可重新生成的用户问题。",
                400,
            )
        regenerate_user = stored_history[last_user_index]
        message = str(regenerate_user.get("content", "")).strip()
        conversation = stored_history[:last_user_index]
    else:
        conversation = history if history is not None else stored_history
    conversation = _normalise_history(conversation)

    if correction:
        effective_input = (
            "【本轮原始需求】\n"
            + (message or "请基于当前项目上下文生成本阶段完整交付物。")
            + "\n\n【顾问纠偏】\n"
            + correction
            + "\n\n请严格按纠偏意见重新生成完整版本，不要只解释修改点。"
        )
    else:
        effective_input = message
    if not effective_input.strip():
        raise ApiError("VALIDATION_ERROR", "请输入内容。", 400)

    sources: List[Dict[str, Any]] = []
    retrieval_warning: Optional[str] = None
    try:
        sources = retrieval_service.search(project_id, effective_input)
    except ApiError as exc:
        if answer_mode == "rag":
            raise
        retrieval_warning = f"文档检索失败，已仅基于项目上下文生成：{exc.message}"

    system_prompt = build_rag_system_prompt(
        project=project,
        stage=stage,
        sources=sources,
        answer_mode=answer_mode,
    )
    return {
        "project": project,
        "project_id": project_id,
        "stage": stage,
        "answer_mode": answer_mode,
        "message": message,
        "correction": correction,
        "effective_input": effective_input,
        "conversation": conversation,
        "sources": sources,
        "system_prompt": system_prompt,
        "warning": retrieval_warning,
        "replace_history": history,
        "regenerate": regenerate,
        "regenerate_user": regenerate_user,
    }


def _persist_exchange(
    context: Dict[str, Any],
    reply: str,
    mode: str,
) -> Dict[str, Any]:
    """写入本轮问答并返回更新后的项目。"""
    timestamp = context_store.now_iso()
    assistant_message = {
        "role": "assistant",
        "content": reply,
        "timestamp": timestamp,
        "sources": context["sources"],
    }
    if context.get("regenerate"):
        user_message = context.get("regenerate_user") or {}
        summary_source = str(user_message.get("content", "")) or context["correction"]
        project = context_store.replace_last_assistant(
            project_id=context["project_id"],
            stage=context["stage"],
            assistant_message=assistant_message,
            last_summary=summarize_text(summary_source),
        )
    else:
        display = (
            context["message"]
            or context["correction"]
            or "（基于当前上下文重新生成）"
        )
        user_message = {
            "role": "user",
            "content": display,
            "timestamp": timestamp,
        }
        summary_source = context["message"] or context["correction"] or reply
        project = context_store.append_exchange(
            project_id=context["project_id"],
            stage=context["stage"],
            user_message=user_message,
            assistant_message=assistant_message,
            replace_history=context["replace_history"],
            last_summary=summarize_text(summary_source),
        )
    if not project:
        raise ApiError(
            "PROJECT_NOT_FOUND",
            "项目不存在，请刷新页面或重新创建项目。",
            404,
        )
    return project


def _local_reply(context: Dict[str, Any]) -> str:
    if context["answer_mode"] == "rag" and not context["sources"]:
        return NO_CONTEXT_REPLY
    if context["answer_mode"] == "rag":
        return build_extractive_rag_reply(context["sources"])
    return build_stage_demo_reply(
        context["system_prompt"],
        context["effective_input"],
    )


def _local_stream(text: str) -> Generator[str, None, None]:
    """本地兜底的打字机分片。"""
    for start in range(0, len(text), 5):
        yield text[start:start + 5]
        time.sleep(0.02)


def _result(
    context: Dict[str, Any],
    reply: str,
    mode: str,
    project: Dict[str, Any],
    warning: Optional[str],
) -> Dict[str, Any]:
    return {
        "reply": reply,
        "sources": context["sources"],
        "grounded": bool(context["sources"]),
        "answer_mode": context["answer_mode"],
        "mode": mode,
        "project": project,
        "warning": warning,
        "history_reset": False,
        "regenerated": bool(context.get("regenerate")),
    }


def handle_chat(
    project_id: str,
    message: str,
    history: Optional[List[Dict[str, Any]]],
    stage: Optional[int],
    answer_mode: str,
    correction: str = "",
    regenerate: bool = False,
) -> Dict[str, Any]:
    """非流式对话，兼容现有调用方。"""
    context = _prepare_chat(
        project_id=project_id,
        message=message,
        history=history,
        stage=stage,
        answer_mode=answer_mode,
        correction=correction,
        regenerate=regenerate,
    )
    sources = context["sources"]
    warning = context["warning"]

    if answer_mode == "rag" and not sources:
        reply = NO_CONTEXT_REPLY
        mode = "no_context"
    else:
        try:
            reply = llm_service.complete(
                system_prompt=context["system_prompt"],
                history=context["conversation"],
                user_message=context["effective_input"],
            )
            mode = "deepseek"
        except LlmNotConfigured:
            reply = _local_reply(context)
            mode = "demo"
        except LlmCallError as exc:
            raise exc.to_api_error() from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("对话服务未处理异常")
            raise ApiError(
                "INTERNAL_ERROR",
                "对话生成失败，请稍后重试。",
                500,
            ) from exc

    if context["stage"] == 5 and context["answer_mode"] == "stage":
        reply = gate_service.ensure_stage5_checklist(reply)
    project = _persist_exchange(context, reply, mode)
    return _result(context, reply, mode, project, warning)


def stream_chat(
    project_id: str,
    message: str,
    history: Optional[List[Dict[str, Any]]],
    stage: Optional[int],
    answer_mode: str,
    correction: str = "",
    regenerate: bool = False,
) -> Generator[Dict[str, Any], None, None]:
    """生成阶段 Agent 的 SSE 事件：meta / sources / delta / done / error。"""
    try:
        context = _prepare_chat(
            project_id=project_id,
            message=message,
            history=history,
            stage=stage,
            answer_mode=answer_mode,
            correction=correction,
            regenerate=regenerate,
        )
    except ApiError as exc:
        yield {"event": "error", "data": exc.to_dict()}
        return
    except Exception:  # noqa: BLE001
        log.exception("阶段对话准备失败")
        yield {
            "event": "error",
            "data": {
                "ok": False,
                "code": "INTERNAL_ERROR",
                "message": "对话初始化失败，请稍后重试。",
                "data": None,
                "error": "对话初始化失败，请稍后重试。",
            },
        }
        return

    sources = context["sources"]
    yield {
        "event": "meta",
        "data": {
            "project_id": context["project_id"],
            "stage": context["stage"],
            "answer_mode": answer_mode,
            "grounded": bool(sources),
            "regenerated": bool(context.get("regenerate")),
        },
    }
    yield {"event": "sources", "data": {"sources": sources}}

    warning = context["warning"]
    reply_parts: List[str] = []
    mode = "demo"

    if answer_mode == "rag" and not sources:
        mode = "no_context"
        for piece in _local_stream(NO_CONTEXT_REPLY):
            reply_parts.append(piece)
            yield {"event": "delta", "data": {"text": piece}}
    elif llm_service.has_api_key():
        try:
            for delta in llm_service.stream_complete(
                system_prompt=context["system_prompt"],
                history=context["conversation"],
                user_message=context["effective_input"],
            ):
                reply_parts.append(delta)
                yield {"event": "delta", "data": {"text": delta}}
            mode = "deepseek"
        except LlmNotConfigured:
            reply_parts = []
        except LlmCallError as exc:
            log.warning("阶段对话流式失败：%s", exc.message)
            yield {"event": "error", "data": exc.to_api_error().to_dict()}
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("阶段对话流式异常")
            yield {
                "event": "error",
                "data": {
                    "ok": False,
                    "code": "INTERNAL_ERROR",
                    "message": "对话生成失败，请稍后重试。",
                    "data": None,
                    "error": "对话生成失败，请稍后重试。",
                },
            }
            return

    if not reply_parts:
        local = _local_reply(context)
        mode = "demo"
        for piece in _local_stream(local):
            reply_parts.append(piece)
            yield {"event": "delta", "data": {"text": piece}}

    reply = "".join(reply_parts)
    if context["stage"] == 5 and context["answer_mode"] == "stage":
        reply = gate_service.ensure_stage5_checklist(reply)
    try:
        project = _persist_exchange(context, reply, mode)
    except ApiError as exc:
        yield {"event": "error", "data": exc.to_dict()}
        return
    except Exception:  # noqa: BLE001
        log.exception("阶段对话保存失败")
        yield {
            "event": "error",
            "data": {
                "ok": False,
                "code": "INTERNAL_ERROR",
                "message": "回答已生成，但保存对话历史失败。",
                "data": None,
                "error": "回答已生成，但保存对话历史失败。",
            },
        }
        return

    yield {
        "event": "done",
        "data": _result(context, reply, mode, project, warning),
    }
