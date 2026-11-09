# -*- coding: utf-8 -*-
"""归档内容自适应导出：Markdown 转 Word / Excel。

解析失败时降级为原始文本写入，保证用户始终能下载到文件。
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from docx import Document
from docx.shared import Pt

from data import context_store
from errors import ApiError
from services.stage_prompts import STAGE_BY_ID


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
NUMBERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")
TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
WORD_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
EXCEL_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


@dataclass
class ExportFile:
    stream: io.BytesIO
    filename: str
    mimetype: str


def _clean_inline(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"__([^_]+)__", r"\1", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）", value)
    return value.strip()


def _table_cells(line: str) -> List[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [_clean_inline(cell) for cell in value.split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(TABLE_SEPARATOR_RE.match(cell.replace(" ", "")) for cell in cells)


def parse_markdown_blocks(markdown: str) -> List[Dict[str, Any]]:
    """把 Markdown 解析为标题/列表/表格/段落/代码块。"""
    lines = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: List[Dict[str, Any]] = []
    index = 0

    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        if not line:
            index += 1
            continue

        if line.startswith("```"):
            index += 1
            code_lines: List[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append({"type": "code", "text": "\n".join(code_lines)})
            continue

        heading = HEADING_RE.match(line)
        if heading:
            blocks.append(
                {
                    "type": "heading",
                    "level": min(len(heading.group(1)), 3),
                    "text": _clean_inline(heading.group(2)),
                }
            )
            index += 1
            continue

        if (
            "|" in line
            and index + 1 < len(lines)
            and _is_table_separator(lines[index + 1])
        ):
            rows = [_table_cells(line)]
            index += 2
            while index < len(lines):
                candidate = lines[index].strip()
                if not candidate or "|" not in candidate:
                    break
                rows.append(_table_cells(candidate))
                index += 1
            width = max((len(row) for row in rows), default=0)
            rows = [row + [""] * (width - len(row)) for row in rows]
            blocks.append({"type": "table", "rows": rows})
            continue

        bullet = BULLET_RE.match(raw)
        if bullet:
            blocks.append(
                {
                    "type": "list",
                    "ordered": False,
                    "text": _clean_inline(bullet.group(1)),
                }
            )
            index += 1
            continue

        numbered = NUMBERED_RE.match(raw)
        if numbered:
            blocks.append(
                {
                    "type": "list",
                    "ordered": True,
                    "number": numbered.group(1),
                    "text": _clean_inline(numbered.group(2)),
                }
            )
            index += 1
            continue

        if line.startswith(">"):
            blocks.append({"type": "paragraph", "text": _clean_inline(line.lstrip("> "))})
            index += 1
            continue

        if re.match(r"^[-*_]{3,}$", line):
            blocks.append({"type": "paragraph", "text": "—"})
            index += 1
            continue

        paragraph_lines = [line]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if not candidate:
                break
            if (
                candidate.startswith("```")
                or HEADING_RE.match(candidate)
                or BULLET_RE.match(lines[index])
                or NUMBERED_RE.match(lines[index])
                or (
                    "|" in candidate
                    and index + 1 < len(lines)
                    and _is_table_separator(lines[index + 1])
                )
            ):
                break
            paragraph_lines.append(candidate)
            index += 1
        blocks.append(
            {"type": "paragraph", "text": _clean_inline(" ".join(paragraph_lines))}
        )
    return blocks


def _safe_component(value: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return text[:80] or "项目"


def _export_context(project_id: str, stage: int) -> Tuple[str, str, str, str]:
    project = context_store.get_project(project_id)
    if not project:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在。", 404)
    stage_meta = STAGE_BY_ID.get(stage)
    if not stage_meta:
        raise ApiError("INVALID_STAGE", "阶段参数必须是 1-5。", 400)
    stage_state = project["stages"].get(str(stage), {})
    if stage_state.get("status") != "archived":
        raise ApiError(
            "EXPORT_NOT_ARCHIVED",
            "请先归档当前阶段内容，再导出。",
            409,
        )
    content = str((stage_state.get("archive") or {}).get("content", "")).strip()
    if not content:
        raise ApiError("EMPTY_ARCHIVE", "当前阶段归档内容为空，无法导出。", 400)
    client_name = str(project.get("client_name") or "未命名客户")
    stage_name = str(stage_meta.get("name") or f"阶段{stage}")
    stem = f"{_safe_component(client_name)}_{_safe_component(stage_name)}_交付物"
    title = f"{client_name} - {stage_name} - 交付物"
    return content, client_name, stage_name, f"{stem}|{title}"


def _fallback_docx(content: str, title: str) -> io.BytesIO:
    document = Document()
    document.add_heading(title, level=0)
    for line in str(content or "").splitlines() or ["（无内容）"]:
        document.add_paragraph(line)
    stream = io.BytesIO()
    document.save(stream)
    stream.seek(0)
    return stream


def _render_word(content: str, title: str) -> io.BytesIO:
    document = Document()
    document.add_heading(title, level=0)
    for block in parse_markdown_blocks(content):
        block_type = block["type"]
        if block_type == "heading":
            document.add_heading(block["text"], level=block["level"])
        elif block_type == "list":
            style = "List Number" if block["ordered"] else "List Bullet"
            document.add_paragraph(block["text"], style=style)
        elif block_type == "table":
            rows = block["rows"]
            if not rows:
                continue
            table = document.add_table(rows=0, cols=len(rows[0]))
            table.style = "Table Grid"
            for row_index, row in enumerate(rows):
                cells = table.add_row().cells
                for cell_index, value in enumerate(row):
                    cells[cell_index].text = str(value)
                    for paragraph in cells[cell_index].paragraphs:
                        if row_index == 0:
                            for run in paragraph.runs:
                                run.bold = True
                        for run in paragraph.runs:
                            run.font.size = Pt(9)
            document.add_paragraph()
        elif block_type == "code":
            for line in str(block.get("text", "")).splitlines():
                paragraph = document.add_paragraph(line)
                for run in paragraph.runs:
                    run.font.name = "Consolas"
                    run.font.size = Pt(9)
        else:
            document.add_paragraph(block.get("text", ""))
    stream = io.BytesIO()
    document.save(stream)
    stream.seek(0)
    return stream


def _display_width(value: Any) -> int:
    text = str(value or "")
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _fallback_xlsx(content: str) -> io.BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "交付物"
    lines = str(content or "").splitlines() or ["（无内容）"]
    for row, line in enumerate(lines, start=1):
        sheet.cell(row=row, column=1, value=line).alignment = Alignment(
            vertical="top", wrap_text=True
        )
    sheet.column_dimensions["A"].width = min(
        max((_display_width(line) for line in lines), default=12) + 2,
        70,
    )
    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def _render_xlsx(content: str, _title: str) -> io.BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "交付物"
    row = 1
    table_header_row: Optional[int] = None
    heading_fill = PatternFill("solid", fgColor="0F766E")
    header_fill = PatternFill("solid", fgColor="D9EFEA")

    for block in parse_markdown_blocks(content):
        block_type = block["type"]
        if block_type == "heading":
            cell = sheet.cell(row=row, column=1, value=block["text"])
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = heading_fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            row += 1
        elif block_type == "table":
            rows = block["rows"]
            if not rows:
                continue
            if table_header_row is None:
                table_header_row = row
            for row_index, values in enumerate(rows):
                for col_index, value in enumerate(values, start=1):
                    cell = sheet.cell(row=row, column=col_index, value=value)
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if row_index == 0:
                        cell.font = Font(bold=True)
                        cell.fill = header_fill
                row += 1
        elif block_type == "list":
            prefix = (
                f"{block.get('number', '')}. "
                if block["ordered"]
                else "• "
            )
            sheet.cell(row=row, column=1, value=prefix + block["text"]).alignment = Alignment(
                vertical="top", wrap_text=True
            )
            row += 1
        elif block_type == "code":
            for line in str(block.get("text", "")).splitlines():
                sheet.cell(row=row, column=1, value=line).alignment = Alignment(
                    vertical="top", wrap_text=True
                )
                row += 1
        else:
            sheet.cell(row=row, column=1, value=block.get("text", "")).alignment = Alignment(
                vertical="top", wrap_text=True
            )
            row += 1

    if table_header_row is not None:
        sheet.freeze_panes = f"A{table_header_row + 1}"

    max_column = max(sheet.max_column, 1)
    for col in range(1, max_column + 1):
        max_width = 12
        for cell_row in range(1, sheet.max_row + 1):
            max_width = max(max_width, _display_width(sheet.cell(cell_row, col).value))
        sheet.column_dimensions[get_column_letter(col)].width = min(max_width + 2, 60)

    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def _build(project_id: str, stage: int, fmt: str) -> ExportFile:
    content, _client, _stage, meta = _export_context(project_id, stage)
    stem, title = meta.split("|", 1)
    fallback = _fallback_docx if fmt == "word" else _fallback_xlsx
    renderer = _render_word if fmt == "word" else _render_xlsx
    try:
        stream = renderer(content, title)
    except Exception:
        # Markdown 结构超出支持范围时，降级为原始文本，保证文件可下载。
        stream = fallback(content, title)
    return ExportFile(
        stream=stream,
        filename=f"{stem}.{'docx' if fmt == 'word' else 'xlsx'}",
        mimetype=WORD_MIME if fmt == "word" else EXCEL_MIME,
    )


def export_word(project_id: str, stage: int) -> ExportFile:
    return _build(project_id, stage, "word")


def export_excel(project_id: str, stage: int) -> ExportFile:
    return _build(project_id, stage, "excel")


def build_word_from_markdown(content: str, title: str, filename: str) -> ExportFile:
    """把任意 Markdown 生成为 Word，供售前评估等独立页面复用。"""
    try:
        stream = _render_word(content, title)
    except Exception:
        stream = _fallback_docx(content, title)
    return ExportFile(stream=stream, filename=filename, mimetype=WORD_MIME)


def build_issues_excel(client_name: str, issues: Sequence[Dict[str, Any]]) -> ExportFile:
    """上线问题跟踪 Excel，复用 openpyxl 样式。"""
    from datetime import datetime

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "上线问题跟踪"
    headers = ["问题描述", "严重程度", "状态", "创建时间", "更新时间"]
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="0F766E")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for issue in issues:
        sheet.append(
            [
                issue.get("description", ""),
                {"high": "高", "medium": "中", "low": "低"}.get(
                    issue.get("severity"), issue.get("severity", "")
                ),
                {
                    "pending": "待处理",
                    "in_progress": "处理中",
                    "resolved": "已解决",
                }.get(issue.get("status"), issue.get("status", "")),
                issue.get("created_at", ""),
                issue.get("updated_at", ""),
            ]
        )
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "A2"
    widths = [50, 12, 12, 22, 22]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    safe_client = _safe_component(client_name)
    stamp = datetime.now().strftime("%Y%m%d")
    return ExportFile(
        stream=stream,
        filename=f"{safe_client}_上线问题跟踪_{stamp}.xlsx",
        mimetype=EXCEL_MIME,
    )
