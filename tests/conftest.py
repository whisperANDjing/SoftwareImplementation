# -*- coding: utf-8 -*-
"""pytest 共享夹具：隔离 projects.json，避免测试污染用户数据。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试数据目录必须与本地真实数据隔离，避免测试写入 projects.json / Chroma。
os.environ["APS_DATA_DIR"] = tempfile.mkdtemp(prefix="aps-tests-")
os.environ["CHROMA_DIR"] = str(Path(os.environ["APS_DATA_DIR"]) / "chroma")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """把上下文存储指向临时目录，并提供干净的上下文 store。"""
    from data import context_store

    monkeypatch.setattr(context_store, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(context_store, "_projects", {})
    return context_store


@pytest.fixture
def project(store):
    """创建一个测试项目并返回项目字典。"""
    return store.create_project(
        {
            "client_name": "测试客户",
            "industry": "装备制造",
            "production_mode": "订单型 MTO",
            "pain_points": "插单多，换型时间长",
        }
    )
