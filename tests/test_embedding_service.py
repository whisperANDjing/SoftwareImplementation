# -*- coding: utf-8 -*-
"""外部 Embedding 服务测试（不访问网络）。"""

from __future__ import annotations

from types import SimpleNamespace

from services import embedding_service


class _FakeEmbeddings:
    def create(self, **kwargs):
        assert kwargs["model"]
        assert kwargs["input"] == ["a", "b"]
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=0, embedding=[3.0, 4.0]),
                SimpleNamespace(index=1, embedding=[0.0, 2.0]),
            ]
        )


class _FakeClient:
    embeddings = _FakeEmbeddings()


def test_external_embedding_normalizes_vectors(monkeypatch):
    monkeypatch.setattr(embedding_service, "is_configured", lambda: True)
    monkeypatch.setattr(embedding_service, "_client", lambda: _FakeClient())
    vectors = embedding_service.embed_texts(["a", "b"])
    assert vectors[0] == [0.6, 0.8]
    assert vectors[1] == [0.0, 1.0]
