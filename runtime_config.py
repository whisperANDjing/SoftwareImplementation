# -*- coding: utf-8 -*-
"""Flask 运行时配置：开发/生产两套 profile。

APP_ENV=development（默认）时适合本地 python app.py；
APP_ENV=production 时由 Gunicorn 启动，监听 0.0.0.0。
"""

from __future__ import annotations

import os

from config import MAX_REQUEST_BYTES


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class BaseRuntimeConfig:
    ENV_NAME = "development"
    DEBUG = False
    HOST = "127.0.0.1"
    PORT = int((os.getenv("PORT") or "5001").strip() or "5001")
    MAX_CONTENT_LENGTH = MAX_REQUEST_BYTES
    JSON_AS_ASCII = False
    PROPAGATE_EXCEPTIONS = False
    PREFERRED_URL_SCHEME = "http"


class DevelopmentConfig(BaseRuntimeConfig):
    ENV_NAME = "development"
    HOST = "127.0.0.1"
    DEBUG = _env_flag("FLASK_DEBUG", "0")


class ProductionConfig(BaseRuntimeConfig):
    ENV_NAME = "production"
    HOST = "0.0.0.0"
    DEBUG = False


def get_runtime_config():
    """按 APP_ENV 选择开发/生产配置。"""
    env = os.getenv("APP_ENV", "development").strip().lower()
    if env in ("production", "prod"):
        return ProductionConfig
    return DevelopmentConfig
