# -*- coding: utf-8 -*-
"""token 用量统计测试。"""

from __future__ import annotations

from types import SimpleNamespace

from services import metrics


def test_metrics_records_usage_and_estimate():
    metrics.reset()
    metrics.record_usage(
        SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    )
    first = metrics.snapshot()
    assert first["prompt_tokens"] == 10
    assert first["completion_tokens"] == 5
    assert first["total_tokens"] == 15
    assert first["calls"] == 1
    assert first["estimated"] is False

    metrics.record_estimate(prompt_chars=20, completion_chars=10)
    second = metrics.snapshot()
    assert second["prompt_tokens"] == 20
    assert second["completion_tokens"] == 10
    assert second["total_tokens"] == 30
    assert second["calls"] == 2
    assert second["estimated"] is True
