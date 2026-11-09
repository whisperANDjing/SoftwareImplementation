# -*- coding: utf-8 -*-
"""来源详情接口数据测试。"""

from __future__ import annotations

import pytest

from data import vector_store
from errors import ApiError
from services import source_service


def _chunk(chunk_id, text, row):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "metadata": {
            "project_id": "prj_test",
            "doc_id": "doc_test",
            "file": "排产表.xlsx",
            "sheet": "排产参数",
            "row_start": row,
            "row_end": row,
            "chunk_index": row,
        },
    }


def test_source_detail_returns_neighbor_context(store, project, monkeypatch):
    monkeypatch.setattr(
        vector_store,
        "get_source_context",
        lambda project_id, chunk_id: {
            "current": _chunk("doc_test:0002", "当前命中片段", 3),
            "before": _chunk("doc_test:0001", "上一段", 2),
            "after": _chunk("doc_test:0003", "下一段", 4),
        },
    )
    source = source_service.get_source(project["id"], "doc_test:0002")
    assert source["file_name"] == "排产表.xlsx"
    assert source["chunk_id"] == "doc_test:0002"
    assert source["row"] == 3
    assert source["before"]["text"] == "上一段"
    assert source["after"]["text"] == "下一段"


def test_source_detail_rejects_missing_chunk(store, project, monkeypatch):
    monkeypatch.setattr(
        vector_store,
        "get_source_context",
        lambda project_id, chunk_id: None,
    )
    with pytest.raises(ApiError) as error:
        source_service.get_source(project["id"], "doc_missing:0000")
    assert error.value.code == "SOURCE_NOT_FOUND"


def test_source_detail_route(store, project, monkeypatch):
    from app import app

    monkeypatch.setattr(
        vector_store,
        "get_source_context",
        lambda project_id, chunk_id: {
            "current": _chunk("doc_test:0001", "命中", 2),
            "before": None,
            "after": None,
        },
    )
    response = app.test_client().get(
        f"/api/source/{project['id']}?chunk_id=doc_test%3A0001"
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["source"]["file_name"] == "排产表.xlsx"
    assert body["source"]["chunk_id"] == "doc_test:0001"
