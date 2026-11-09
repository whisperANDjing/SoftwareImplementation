# -*- coding: utf-8 -*-
"""开发/生产运行配置测试。"""

from __future__ import annotations

from runtime_config import get_runtime_config


def test_development_profile(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    config = get_runtime_config()
    assert config.ENV_NAME == "development"
    assert config.HOST == "127.0.0.1"
    assert config.DEBUG is False


def test_production_profile(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    config = get_runtime_config()
    assert config.ENV_NAME == "production"
    assert config.HOST == "0.0.0.0"
    assert config.DEBUG is False
