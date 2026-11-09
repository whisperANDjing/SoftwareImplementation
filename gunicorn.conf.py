# -*- coding: utf-8 -*-
"""Gunicorn 生产配置。

本地 Chroma 使用单进程持久化，默认 1 worker + 多线程，避免多进程争用 SQLite 索引。
"""

from __future__ import annotations

import os


bind = f"0.0.0.0:{os.getenv('PORT', '5001')}"
worker_class = "gthread"
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
threads = int(os.getenv("GUNICORN_THREADS", "8"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "300"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
capture_output = True
preload_app = False
