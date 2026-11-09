# -*- coding: utf-8 -*-
"""检索服务：外部 Embedding（可选）+ 内存切片索引。

没有 Embedding API Key 或 API 失败时，使用内存中的关键词检索；不加载本地模型。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from config import RAG_MIN_SCORE, RAG_RELEVANCE_SCORE, RAG_TOP_K
from data import document_store, vector_store
from errors import ApiError
from services import embedding_service


log = logging.getLogger("aps-workbench.retrieval")

# 只用于“问题里是否包含可匹配内容词”的轻量判断，不做完整中文分词。
_STOP_PHRASES = (
    "这个文件里",
    "这份文件里",
    "文件里",
    "文档里",
    "这个文件",
    "这份文件",
    "请问",
    "帮我",
    "看一下",
    "看看",
    "告诉我",
    "是多少",
    "有多少台",
    "有多少",
    "有哪些",
    "是什么",
    "有什么",
    "有没有",
    "怎么样",
    "如何",
    "什么",
    "多少",
    "哪些",
    "这个",
    "那个",
    "一下",
    "是否",
    "以及",
    "还有",
    "文件",
    "文档",
    "里面",
    "里",
    "的",
    "了",
    "吗",
    "呢",
    "请",
    "和",
    "与",
    "及",
)
_EN_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "what", "which",
    "how", "many", "much", "of", "in", "on", "for", "to", "and",
    "please", "tell", "me", "this", "that", "file", "document",
}


def _content_terms(query: str) -> List[str]:
    """提取用于关键词命中的中文 2/3-gram 与英文单词。"""
    text = query.lower()
    for phrase in _STOP_PHRASES:
        text = text.replace(phrase, " ")
    parts = re.split(r"[\s，。！？、,.!?;；:：()（）\[\]【】\"'“”‘’]+", text)
    terms: List[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if re.search(r"[\u4e00-\u9fff]", part):
            if len(part) >= 2:
                for size in (2, 3):
                    for index in range(0, len(part) - size + 1):
                        terms.append(part[index:index + size])
        elif len(part) >= 3 and part not in _EN_STOP:
            terms.append(part)
    return list(dict.fromkeys(terms))


def _has_keyword_hit(query: str, text: str) -> bool:
    terms = _content_terms(query)
    if not terms:
        return False
    lowered = text.lower()
    return any(term in lowered for term in terms)


def search(project_id: str, query: str) -> List[Dict[str, Any]]:
    """按项目检索 top-k 文档片段。

    输入：项目 ID、用户问题
    输出：来源列表，字段满足 API 契约；没有文档或没有命中时返回 []
    """
    if not query.strip():
        return []
    if document_store.count_indexed(project_id) == 0:
        return []

    query_vector = embedding_service.try_embed_query(query)
    try:
        matches = vector_store.query_chunks(
            project_id=project_id,
            query_embedding=query_vector,
            query_text=query,
            top_k=RAG_TOP_K,
            min_score=RAG_MIN_SCORE,
        )
    except vector_store.VectorStoreError as exc:
        raise ApiError(
            "VECTOR_STORE_FAILED",
            f"向量库检索失败：{exc}",
            500,
        ) from exc

    sources: List[Dict[str, Any]] = []
    for match in matches:
        metadata = match.get("metadata", {})
        text = str(match.get("text", ""))
        score = float(match.get("score", 0.0))
        keyword_hit = _has_keyword_hit(query, text)
        # 弱语义结果只有在问题中的内容词确实出现在片段中时才保留，
        # 避免“食堂菜单”这类无关问题被低分片段误召回。
        if score < RAG_RELEVANCE_SCORE and not keyword_hit:
            continue
        file_name = str(metadata.get("file", "未知文件"))
        source = {
            "file_name": file_name,
            "chunk_id": match.get("chunk_id", ""),
            "snippet": text[:500],
            "score": round(score, 4),
            # 兼容旧前端字段
            "file": file_name,
            "doc_id": metadata.get("doc_id"),
            "stage": metadata.get("stage"),
        }
        for field in (
            "page",
            "sheet",
            "row_start",
            "row_end",
            "paragraph_start",
            "paragraph_end",
            "chunk_index",
        ):
            if metadata.get(field) is not None:
                source[field] = metadata[field]
        if metadata.get("row_start") is not None:
            source["row"] = metadata["row_start"]
        if metadata.get("paragraph_start") is not None:
            source["paragraph"] = metadata["paragraph_start"]
        sources.append(source)

    log.info("项目 %s 检索命中 %s 条（top_k=%s）", project_id, len(sources), RAG_TOP_K)
    return sources
