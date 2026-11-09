# -*- coding: utf-8 -*-
"""文档解析与切片测试。"""

from __future__ import annotations

from openpyxl import Workbook

from services.document_service import _chunk_blocks, _parse_xlsx


def test_xlsx_parse_and_chunk_preserves_sheet_rows(tmp_path):
    path = tmp_path / "排产计划.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "排产参数"
    sheet.append(["参数", "值", "说明"])
    sheet.append(["排产周期（天）", 7, "按周滚动"])
    sheet.append(["关键设备数量（台）", 12, "含 3 台并行机"])
    other = workbook.create_sheet("插单记录")
    other.append(["日期", "订单号", "原因"])
    other.append(["2026-10-03", "SO-1001", "客户催单"])
    workbook.save(path)

    blocks = _parse_xlsx(path)
    chunks = _chunk_blocks(blocks)

    assert len(chunks) >= 2
    assert any("排产周期（天）" in chunk["text"] for chunk in chunks)
    assert any(chunk["metadata"].get("sheet") == "插单记录" for chunk in chunks)
    assert all(len(chunk["text"]) <= 900 for chunk in chunks)
    assert all("row_start" in chunk["metadata"] for chunk in chunks)
    assert all("row_end" in chunk["metadata"] for chunk in chunks)
    assert [chunk["metadata"]["chunk_index"] for chunk in chunks] == list(
        range(len(chunks))
    )
