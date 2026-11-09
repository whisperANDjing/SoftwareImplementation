# -*- coding: utf-8 -*-
"""内存切片索引测试：不依赖 Chroma。"""

from __future__ import annotations

from data import vector_store


def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(vector_store, "VECTOR_INDEX_FILE", tmp_path / "index.json")
    monkeypatch.setattr(vector_store, "_chunks", {})


def test_memory_index_lexical_and_vector_query(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    vector_store.add_chunks(
        "prj_test",
        "doc_test",
        [
            {"text": "排产周期 7 天", "metadata": {"chunk_index": 0}},
            {"text": "食堂菜单", "metadata": {"chunk_index": 1}},
        ],
        embeddings=None,
    )
    lexical = vector_store.query_chunks(
        project_id="prj_test",
        query_text="排产周期是多少",
        top_k=5,
        min_score=0.35,
    )
    assert lexical[0]["text"] == "排产周期 7 天"

    vector_store.add_chunks(
        "prj_test",
        "doc_vec",
        [{"text": "向量命中", "metadata": {"chunk_index": 0}}],
        embeddings=[[1.0, 0.0]],
    )
    vector = vector_store.query_chunks(
        project_id="prj_test",
        query_embedding=[1.0, 0.0],
        query_text="无关",
        top_k=5,
        min_score=0.35,
    )
    assert vector[0]["text"] == "向量命中"
    context = vector_store.get_source_context("prj_test", "doc_test:0000")
    assert context["current"]["text"] == "排产周期 7 天"
    assert context["after"]["text"] == "食堂菜单"


def test_memory_index_delete_project(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    vector_store.add_chunks(
        "prj_delete",
        "doc_delete",
        [{"text": "待删除片段", "metadata": {"chunk_index": 0}}],
        embeddings=None,
    )
    assert vector_store.delete_project("prj_delete") == 1
    assert vector_store.collection_count() == 0
