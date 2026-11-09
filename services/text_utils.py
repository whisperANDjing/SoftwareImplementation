# -*- coding: utf-8 -*-
"""文本摘要辅助函数。"""

from __future__ import annotations

import re


def summarize_text(text: str, limit: int = 50) -> str:
    """把任意文本压缩成单行摘要，超过 limit 时追加省略号。"""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + "…"
