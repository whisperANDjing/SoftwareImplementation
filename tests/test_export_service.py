# -*- coding: utf-8 -*-
"""Markdown 自适应导出 Word / Excel 测试。"""

from __future__ import annotations

import io

from docx import Document
from openpyxl import load_workbook

from app import app
from services import export_service


MARKDOWN = """# 调研结论

## 关键参数

- 排产周期 7 天
- 计划冻结期 1 天

1. 先确认产能
2. 再确认物料

| 参数 | 值 | 说明 |
| --- | --- | --- |
| 排产周期 | 7 | 按周滚动 |
| 关键设备 | 12 | 含并行机 |

普通段落用于验证 Normal 样式。
"""


def _archived_project(store, project):
    store.archive_stage(project["id"], 1, MARKDOWN, last_summary="测试")
    return project


def test_word_export_builds_headings_lists_and_table(store, project):
    _archived_project(store, project)
    result = export_service.export_word(project["id"], 1)
    document = Document(io.BytesIO(result.stream.getvalue()))
    style_names = [paragraph.style.name for paragraph in document.paragraphs]

    assert result.filename.endswith("_交付物.docx")
    assert "Title" in style_names
    assert "Heading 1" in style_names
    assert "Heading 2" in style_names
    assert "List Bullet" in style_names
    assert "List Number" in style_names
    assert len(document.tables) == 1
    assert document.tables[0].style.name == "Table Grid"
    assert document.tables[0].cell(0, 0).text == "参数"
    assert document.tables[0].cell(1, 0).text == "排产周期"


def test_excel_export_builds_table_widths_and_freeze(store, project):
    _archived_project(store, project)
    result = export_service.export_excel(project["id"], 1)
    workbook = load_workbook(io.BytesIO(result.stream.getvalue()))
    sheet = workbook.active
    values = [sheet.cell(row=index, column=1).value for index in range(1, sheet.max_row + 1)]

    assert result.filename.endswith("_交付物.xlsx")
    assert "调研结论" in values
    assert "参数" in values
    assert "排产周期" in values
    header_row = values.index("参数") + 1
    assert sheet.cell(header_row, 1).font.bold is True
    assert sheet.freeze_panes == f"A{header_row + 1}"
    assert sheet.column_dimensions["A"].width >= 12


def test_word_export_falls_back_to_raw_text(store, project, monkeypatch):
    _archived_project(store, project)

    def fail_render(content, title):
        raise ValueError("模拟 Markdown 解析失败")

    monkeypatch.setattr(export_service, "_render_word", fail_render)
    result = export_service.export_word(project["id"], 1)
    document = Document(io.BytesIO(result.stream.getvalue()))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "调研结论" in text
    assert "| 参数 | 值 | 说明 |" in text


def test_export_routes_require_archive(store, project):
    client = app.test_client()
    response = client.post(
        "/api/export/word",
        json={"project_id": project["id"], "stage": 1},
    )
    body = response.get_json()
    assert response.status_code == 409
    assert body["code"] == "EXPORT_NOT_ARCHIVED"


def test_export_routes_return_files(store, project):
    _archived_project(store, project)
    client = app.test_client()
    for fmt, prefix in (("word", "PK"), ("excel", "PK")):
        response = client.post(
            f"/api/export/{fmt}",
            json={"project_id": project["id"], "stage": 1},
        )
        assert response.status_code == 200
        assert response.data.startswith(prefix.encode())
        assert "attachment" in response.headers["Content-Disposition"]
