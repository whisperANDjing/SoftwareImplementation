# -*- coding: utf-8 -*-
"""全局 AI 小助手服务。

与阶段 Agent 的区别：
- 不绑定 stage，不读写阶段对话历史；
- 默认先检索项目知识库，检索为空或失败时允许通用模型回答；
- 历史单独存放在 data/assistant_store.py；
- 同时支持普通 JSON 与 SSE 流式返回。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

from config import MAX_HISTORY_MESSAGES
from data import assistant_store, context_store
from errors import ApiError, LlmCallError, LlmNotConfigured
from services import llm_service, project_deletion_service, retrieval_service
from services.demo_service import build_extractive_rag_reply
from services.stage_prompts import (
    APS_DOMAIN_RULES,
    format_retrieved_sources,
    project_context_prompt,
)


log = logging.getLogger("aps-workbench.assistant")


ASSISTANT_SYSTEM_PROMPT = """你是「APS 实施顾问 AI 工作台」的全局 AI 小助手，可以随时回答用户关于 APS、排产、项目推进和当前客户的问题。

## 回答原则
1. 先判断问题是否与当前项目知识库有关；如果检索到了文档片段，优先使用片段回答，并用 [1]、[2] 标注引用。
2. 如果检索为空或检索片段不足以回答，则使用通用 APS 知识与通用大模型能力回答；此时必须明确说明“当前知识库未检索到直接依据”，不得把通用建议包装成项目文档事实。
3. 不得编造客户数据、文件名、页码、行号、指标数值或系统能力。
4. 回答直接、简洁、可执行，使用中文 Markdown；不要套用五阶段交付物的固定章节模板。
5. 如果用户问题与 APS 无关，也可以正常回答，但要说明这是通用助手回答，不表示项目文档中有此内容。
6. 项目归档是项目背景，不是文档来源；只有 sources 中的片段可作为文档依据。"""


def _local_general_reply(project: Dict[str, Any], message: str) -> str:
    """无 Key 且知识库未命中时的本地通用兜底。"""
    client = project.get("client_name") or "当前项目"
    text = message.strip()
    greeting = any(word in text for word in ("你好", "您好", "hello", "hi"))
    if greeting:
        return (
            f"你好，我是 {client} 的全局 AI 小助手。"
            "你可以问我项目文档、APS 排产规则、实施推进或一般性问题。"
            "当前未配置 `DEEPSEEK_API_KEY`，通用问答暂由本地演示模式兜底；"
            "上传文档后，涉及项目事实的问题会优先检索知识库。"
        )
    return (
        f"当前知识库未检索到与「{text}」直接相关的文档片段。\n\n"
        "当前未配置 `DEEPSEEK_API_KEY`，暂时无法调用通用大模型；"
        "以下是本地演示模式下的通用提示：\n\n"
        "- 可以先补充相关项目文档，或换一种更具体的问法。\n"
        "- 涉及 APS 方案时，请同时说明计划层级、设备/模具/人员/物料约束和异常场景。\n"
        "- 需要真实通用回答时，请配置 DeepSeek Key 后重试。"
    )


def _prepare(project_id: str, message: str) -> Dict[str, Any]:
    """加载项目、检索知识库并准备历史与系统提示词。"""
    project = context_store.get_project(project_id)
    if not project:
        raise ApiError(
            "PROJECT_NOT_FOUND",
            "项目不存在，请刷新页面或重新创建项目。",
            404,
        )
    project_deletion_service.ensure_not_deleting(project_id)

    warning: Optional[str] = None
    sources: List[Dict[str, Any]] = []
    try:
        sources = retrieval_service.search(project_id, message)
    except ApiError as exc:
        # 小助手的通用问答允许在检索失败时降级到通用模型。
        warning = f"知识库检索失败，已改为通用回答：{exc.message}"
        log.warning(warning)
    except Exception as exc:  # noqa: BLE001
        warning = "知识库检索异常，已改为通用回答。"
        log.exception("小助手检索异常")

    stage = int(project.get("active_stage", 1))
    context = project_context_prompt(project, stage)
    source_block = format_retrieved_sources(sources)

    prompt_parts = [
        ASSISTANT_SYSTEM_PROMPT,
        APS_DOMAIN_RULES,
        context,
        source_block,
    ]
    if sources:
        prompt_parts.append(
            """## 本轮知识库检索结果
- 本轮已检索到文档片段，优先依据这些片段回答。
- 基于片段的事实必须带 [n] 引用；通用建议可以补充，但必须标注“通用建议”。"""
        )
    else:
        prompt_parts.append(
            """## 本轮知识库检索结果
- 本轮没有检索到可用文档片段。
- 可以直接使用通用 APS 知识回答，但必须说明“当前知识库未检索到直接依据”。
- 不要虚构项目文档内容。"""
        )

    history = assistant_store.last_messages(project_id, MAX_HISTORY_MESSAGES)
    return {
        "project": project,
        "sources": sources,
        "history": history,
        "system_prompt": "\n\n".join(prompt_parts),
        "warning": warning,
    }


def _fallback_reply(
    project: Dict[str, Any],
    message: str,
    sources: List[Dict[str, Any]],
) -> str:
    if sources:
        return build_extractive_rag_reply(sources)
    return _local_general_reply(project, message)


def _persist(
    project_id: str,
    message: str,
    reply: str,
    sources: List[Dict[str, Any]],
    mode: str,
) -> Optional[str]:
    """保存独立助手历史；失败只记录 warning，不影响回答。"""
    timestamp = assistant_store.now_iso()
    try:
        assistant_store.append_exchange(
            project_id,
            {
                "role": "user",
                "content": message,
                "ts": timestamp,
            },
            {
                "role": "assistant",
                "content": reply,
                "ts": timestamp,
                "sources": sources,
                "mode": mode,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("小助手历史保存失败")
        return f"小助手历史保存失败：{type(exc).__name__}"
    return None


def _generate_sync(
    project: Dict[str, Any],
    message: str,
    history: List[Dict[str, str]],
    system_prompt: str,
    sources: List[Dict[str, Any]],
    warning: Optional[str],
) -> Tuple[str, str, Optional[str]]:
    """非流式生成；返回 (reply, mode, warning)。"""
    if llm_service.has_api_key():
        try:
            reply = llm_service.complete(
                system_prompt,
                history,
                message,
                max_tokens=1_200,
                temperature=0.4,
            )
            return reply, "deepseek", warning
        except LlmNotConfigured:
            pass
        except LlmCallError as exc:
            log.warning("小助手 LLM 失败，使用本地兜底：%s", exc.message)
            fallback_warning = f"DeepSeek 调用失败，已使用本地兜底：{exc.message}"
            reply = _fallback_reply(project, message, sources)
            return reply, "demo-fallback", _merge_warning(warning, fallback_warning)

    reply = _fallback_reply(project, message, sources)
    return reply, "demo", warning


def _merge_warning(first: Optional[str], second: Optional[str]) -> Optional[str]:
    if first and second:
        return f"{first}；{second}"
    return first or second


def _stream_text(text: str, chunk_size: int = 5) -> Generator[str, None, None]:
    """把本地兜底文本切成小片，模拟打字机。"""
    for start in range(0, len(text), chunk_size):
        yield text[start:start + chunk_size]
        time.sleep(0.02)


def handle_chat(project_id: str, message: str) -> Dict[str, Any]:
    """非流式助手对话，供测试和兼容客户端使用。"""
    prepared = _prepare(project_id, message)
    project = prepared["project"]
    sources = prepared["sources"]
    reply, mode, warning = _generate_sync(
        project,
        message,
        prepared["history"],
        prepared["system_prompt"],
        sources,
        prepared["warning"],
    )
    persistence_warning = _persist(project_id, message, reply, sources, mode)
    warning = _merge_warning(warning, persistence_warning)
    return {
        "reply": reply,
        "sources": sources,
        "grounded": bool(sources),
        "answer_mode": "rag",
        "mode": mode,
        "warning": warning,
    }


def stream_chat(project_id: str, message: str) -> Generator[Dict[str, Any], None, None]:
    """生成 SSE 事件字典：meta / sources / delta / done / error。"""
    try:
        prepared = _prepare(project_id, message)
    except ApiError as exc:
        yield {"event": "error", "data": exc.to_dict()}
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("小助手准备阶段失败")
        yield {
            "event": "error",
            "data": {
                "ok": False,
                "code": "INTERNAL_ERROR",
                "message": "小助手初始化失败，请稍后重试。",
                "data": None,
                "error": "小助手初始化失败，请稍后重试。",
            },
        }
        return

    project = prepared["project"]
    sources = prepared["sources"]
    warning = prepared["warning"]
    yield {
        "event": "meta",
        "data": {
            "project_id": project_id,
            "answer_mode": "rag",
            "grounded": bool(sources),
        },
    }
    yield {"event": "sources", "data": {"sources": sources}}

    reply_parts: List[str] = []
    mode = "demo"

    if llm_service.has_api_key():
        try:
            for delta in llm_service.stream_complete(
                prepared["system_prompt"],
                prepared["history"],
                message,
                max_tokens=1_200,
                temperature=0.4,
            ):
                reply_parts.append(delta)
                yield {"event": "delta", "data": {"text": delta}}
            mode = "deepseek"
        except LlmNotConfigured:
            reply_parts = []
        except LlmCallError as exc:
            log.warning("小助手流式 LLM 失败：%s", exc.message)
            if reply_parts:
                yield {
                    "event": "error",
                    "data": {
                        "ok": False,
                        "code": exc.code,
                        "message": exc.message,
                        "data": None,
                        "error": exc.message,
                    },
                }
                return
            warning = _merge_warning(
                warning,
                f"DeepSeek 调用失败，已使用本地兜底：{exc.message}",
            )
            mode = "demo-fallback"

    if not reply_parts:
        fallback = _fallback_reply(project, message, sources)
        if mode == "demo" and not llm_service.has_api_key():
            mode = "demo"
        for piece in _stream_text(fallback):
            reply_parts.append(piece)
            yield {"event": "delta", "data": {"text": piece}}

    reply = "".join(reply_parts)
    persistence_warning = _persist(project_id, message, reply, sources, mode)
    warning = _merge_warning(warning, persistence_warning)
    yield {
        "event": "done",
        "data": {
            "reply": reply,
            "sources": sources,
            "grounded": bool(sources),
            "answer_mode": "rag",
            "mode": mode,
            "warning": warning,
        },
    }


def get_history(project_id: str) -> List[Dict[str, Any]]:
    """返回独立助手历史；与阶段历史完全隔离。"""
    if not context_store.get_project(project_id):
        raise ApiError(
            "PROJECT_NOT_FOUND",
            "项目不存在，请刷新页面或重新创建项目。",
            404,
        )
    return assistant_store.get_messages(project_id)
