# -*- coding: utf-8 -*-
"""检索服务测试：来源映射、阈值与关键词兜底。"""

from __future__ import annotations

from data import document_store, vector_store
from services import embedding_service, retrieval_service


def _match(score, snippet, row=2):
    return {
        "chunk_id": "doc_test:0000",
        "text": snippet,
        "score": score,
        "metadata": {
            "file": "排产计划.xlsx",
            "doc_id": "doc_test",
            "stage": 2,
            "sheet": "排产参数",
            "row_start": row,
            "row_end": row,
            "chunk_index": 0,
        },
    }


def test_retrieval_maps_source_and_drops_weak_unrelated(monkeypatch):
    monkeypatch.setattr(document_store, "count_indexed", lambda project_id: 1)
    monkeypatch.setattr(embedding_service, "embed_query", lambda query: [0.1, 0.2])
    monkeypatch.setattr(
        vector_store,
        "query_chunks",
        lambda **kwargs: [
            _match(0.70, "排产周期 7 天；计划冻结期 1 天。"),
            _match(0.42, "食堂今日供应面条和米饭。"),
        ],
    )

    result = retrieval_service.search("prj_test", "这个文件里排产周期是多少？")

    assert len(result) == 1
    assert result[0]["file_name"] == "排产计划.xlsx"
    assert result[0]["file"] == "排产计划.xlsx"
    assert result[0]["chunk_id"] == "doc_test:0000"
    assert result[0]["sheet"] == "排产参数"
    assert result[0]["row"] == 2
    assert result[0]["score"] == 0.7
    assert "排产周期" in result[0]["snippet"]


def test_retrieval_keeps_keyword_hit_below_semantic_threshold(monkeypatch):
    monkeypatch.setattr(document_store, "count_indexed", lambda project_id: 1)
    monkeypatch.setattr(embedding_service, "embed_query", lambda query: [0.1, 0.2])
    monkeypatch.setattr(
        vector_store,
        "query_chunks",
        lambda **kwargs: [
            _match(0.46, "关键设备数量：12 台。", row=4),
            _match(0.44, "今日食堂菜单。", row=99),
        ],
    )

    result = retrieval_service.search("prj_test", "关键设备有多少台？")

    assert len(result) == 1
    assert result[0]["row_start"] == 4
    assert "关键设备" in result[0]["snippet"]
