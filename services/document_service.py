# -*- coding: utf-8 -*-
"""文档服务：解析、切片、向量化、写入 Chroma、生成摘要与登记状态。"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from config import (
    CHUNK_MAX_CHARS,
    CHUNK_OVERLAP_CHARS,
    CHUNK_TARGET_CHARS,
    MAX_FILENAME_CHARS,
    MAX_UPLOAD_BYTES,
    STAGE_IDS,
    SUMMARY_MAX_INPUT_CHARS,
    SUMMARY_MAX_TOKENS,
    SUPPORTED_EXTENSIONS,
    UPLOAD_DIR,
)
from data import context_store, document_store, vector_store
from errors import ApiError, LlmCallError, LlmNotConfigured
from services import embedding_service, llm_service, project_deletion_service
from services.stage_prompts import STAGE_BY_ID


log = logging.getLogger("aps-workbench.document")


# ---------------------------------------------------------------------------
# 文件解析
# ---------------------------------------------------------------------------

def _normalise_text(text: str) -> str:
    value = str(text or "").replace("\x00", " ")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value.strip()


def _split_paragraphs(text: str) -> List[str]:
    """按空行拆段；没有空行时按行拆分，避免整页成为超长块。"""
    normalised = _normalise_text(text)
    if not normalised:
        return []
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", normalised) if item.strip()]
    if len(paragraphs) == 1 and "\n" in paragraphs[0]:
        paragraphs = [line.strip() for line in paragraphs[0].splitlines() if line.strip()]
    return paragraphs


def _parse_docx(path: Path) -> List[Dict[str, Any]]:
    """使用 wizarddocx 抽取 DOCX 文本，按行生成带段落序号的基础块。"""
    try:
        from wizarddocx import extract_text
    except ImportError as exc:  # pragma: no cover
        raise ApiError(
            "PARSE_FAILED",
            "未安装 wizarddocx，无法解析 DOCX。",
            422,
        ) from exc

    text = _normalise_text(extract_text(str(path)))
    if not text:
        raise ApiError("EMPTY_DOCUMENT", "DOCX 中没有可入库的文本。", 422)

    blocks: List[Dict[str, Any]] = []
    for index, paragraph in enumerate(_split_paragraphs(text), start=1):
        blocks.append(
            {
                "text": paragraph,
                "meta": {"paragraph_start": index, "paragraph_end": index},
                "segment": "docx",
            }
        )
    return blocks


def _parse_pdf(path: Path) -> List[Dict[str, Any]]:
    """使用 pdfplumber-rs 逐页抽取数字版 PDF 文本。"""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise ApiError(
            "PARSE_FAILED",
            "未安装 pdfplumber-rs，无法解析 PDF。",
            422,
        ) from exc

    blocks: List[Dict[str, Any]] = []
    total_text = 0
    page_count = 0
    try:
        pdf = pdfplumber.open(str(path))
        try:
            pages = list(pdf.pages)
            page_count = len(pages)
            for page_number, page in enumerate(pages, start=1):
                text = _normalise_text(page.extract_text() or "")
                total_text += len(text)
                for paragraph in _split_paragraphs(text):
                    blocks.append(
                        {
                            "text": paragraph,
                            "meta": {"page": page_number},
                            "segment": f"page:{page_number}",
                        }
                    )
        finally:
            close = getattr(pdf, "close", None)
            if callable(close):
                close()
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("PDF 解析失败")
        raise ApiError(
            "PARSE_FAILED",
            f"PDF 解析失败：{type(exc).__name__}。请确认文件未加密且未损坏。",
            422,
        ) from exc

    if page_count and total_text < 20:
        raise ApiError(
            "SCANNED_PDF_UNSUPPORTED",
            "该 PDF 可能是扫描件，当前版本不支持 OCR。"
            "请上传含文本层的数字版 PDF，或先手动转为可复制文本的 PDF。",
            422,
        )
    if not blocks:
        raise ApiError("EMPTY_DOCUMENT", "PDF 中没有可入库的文本。", 422)
    return blocks


def _looks_like_header(values: Sequence[str]) -> bool:
    """简单判断首行是否像表头：至少两个非空单元格，且没有明显数值。"""
    if len(values) < 2:
        return False
    text_values = [value for value in values if value]
    if len(text_values) < 2:
        return False
    numeric = 0
    for value in text_values:
        try:
            float(str(value).replace(",", "").strip())
            numeric += 1
        except ValueError:
            continue
    return numeric == 0


def _parse_xlsx(path: Path) -> List[Dict[str, Any]]:
    """遍历 Excel 所有 sheet，保留工作表名与行号。"""
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise ApiError(
            "PARSE_FAILED",
            "未安装 openpyxl，无法解析 XLSX。",
            422,
        ) from exc

    try:
        workbook = openpyxl.load_workbook(
            filename=str(path),
            data_only=True,
            read_only=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Excel 打开失败")
        raise ApiError(
            "PARSE_FAILED",
            f"Excel 文件解析失败：{type(exc).__name__}。"
            "请确认文件未加密且格式正确。",
            422,
        ) from exc

    blocks: List[Dict[str, Any]] = []
    try:
        for worksheet in workbook.worksheets:
            rows: List[Tuple[int, List[str]]] = []
            for row_index, row in enumerate(
                worksheet.iter_rows(values_only=True), start=1
            ):
                values = [
                    str(value).strip()
                    for value in row
                    if value is not None and str(value).strip() != ""
                ]
                if values:
                    rows.append((row_index, values))
            if not rows:
                continue

            use_header = len(rows) > 1 and _looks_like_header(rows[0][1])
            header = rows[0][1] if use_header else []
            data_rows = rows[1:] if use_header else rows
            if use_header and not data_rows:
                data_rows = [rows[0]]

            for row_index, values in data_rows:
                if use_header and header:
                    fields = []
                    for column_index, value in enumerate(values):
                        key = (
                            header[column_index]
                            if column_index < len(header)
                            else f"列{column_index + 1}"
                        )
                        fields.append(f"{key}: {value}")
                    content = " | ".join(fields)
                else:
                    content = " | ".join(values)
                text = f"工作表「{worksheet.title}」第{row_index}行：{content}"
                blocks.append(
                    {
                        "text": text,
                        "meta": {
                            "sheet": worksheet.title,
                            "row_start": row_index,
                            "row_end": row_index,
                        },
                        "segment": f"sheet:{worksheet.title}",
                    }
                )
    finally:
        workbook.close()

    if not blocks:
        raise ApiError("EMPTY_DOCUMENT", "Excel 中没有可入库的文本。", 422)
    return blocks


def _parse_file(path: Path, file_type: str) -> List[Dict[str, Any]]:
    if file_type == "docx":
        return _parse_docx(path)
    if file_type == "pdf":
        return _parse_pdf(path)
    if file_type == "xlsx":
        return _parse_xlsx(path)
    raise ApiError("UNSUPPORTED_FILE_TYPE", "不支持的文件类型。", 415)


# ---------------------------------------------------------------------------
# 切片
# ---------------------------------------------------------------------------

def _split_long_block(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把超长段落按句子/字符拆开，保持来源元数据。"""
    text = block["text"]
    if len(text) <= CHUNK_MAX_CHARS:
        return [block]

    pieces: List[str] = []
    current = ""
    for sentence in re.split(r"(?<=[。！？；;.!?])\s*", text):
        if not sentence:
            continue
        if len(current) + len(sentence) <= CHUNK_MAX_CHARS - 50:
            current += sentence
        else:
            if current:
                pieces.append(current)
            while len(sentence) > CHUNK_MAX_CHARS - 50:
                pieces.append(sentence[:CHUNK_MAX_CHARS - 50])
                sentence = sentence[CHUNK_MAX_CHARS - 50:]
            current = sentence
    if current:
        pieces.append(current)

    return [
        {**block, "text": piece.strip()}
        for piece in pieces
        if piece.strip()
    ]


def _merge_meta(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    first = blocks[0]["meta"]
    last = blocks[-1]["meta"]
    meta = dict(first)
    for key in ("page", "sheet"):
        if last.get(key) is not None:
            meta[key] = last[key]
    for start_key, end_key in (
        ("row_start", "row_end"),
        ("paragraph_start", "paragraph_end"),
    ):
        if first.get(start_key) is not None:
            meta[start_key] = first[start_key]
        if last.get(end_key) is not None:
            meta[end_key] = last[end_key]
    return meta


def _chunk_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按段落/表格行组切片，目标 600–900 字符，带约 100 字符重叠。"""
    target = CHUNK_TARGET_CHARS
    max_chars = CHUNK_MAX_CHARS
    overlap_size = CHUNK_OVERLAP_CHARS
    chunks: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    current_len = 0
    current_segment = ""
    overlap = ""

    def emit() -> None:
        nonlocal overlap
        if not current:
            return
        body = "\n".join(item["text"] for item in current)
        text = (overlap + "\n" + body).strip() if overlap else body
        if len(text) > max_chars:
            text = text[:max_chars]
        if text:
            chunks.append(
                {
                    "text": text,
                    "metadata": _merge_meta(current),
                    "segment": current_segment,
                }
            )
            overlap = text[-overlap_size:]

    for block in blocks:
        for piece in _split_long_block(block):
            piece_segment = piece.get("segment", "")
            if current and (
                piece_segment != current_segment
                or current_len + len(piece["text"]) + 1 > max_chars
            ):
                emit()
                if piece_segment != current_segment:
                    overlap = ""
                current = []
                current_len = 0
            if not current:
                current_segment = piece_segment
            current.append(piece)
            current_len += len(piece["text"]) + 1
            if current_len >= target:
                emit()
                current = []
                current_len = 0

    emit()
    for index, chunk in enumerate(chunks):
        chunk["metadata"]["chunk_index"] = index
    return chunks


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------

def _extractive_summary(file_name: str, stage: int, chunks: List[Dict[str, Any]]) -> str:
    preview = "\n\n".join(
        chunk["text"] for chunk in chunks[:2]
    )[:900]
    return (
        f"## 文档摘要（抽取式）\n\n"
        f"- 文件：{file_name}\n"
        f"- 阶段：阶段{stage} · {STAGE_BY_ID[stage]['name']}\n"
        f"- 入库切片：{len(chunks)} 个\n\n"
        f"### 主要内容预览\n{preview}"
    )


def _generate_summary(
    project: Dict[str, Any],
    stage: int,
    file_name: str,
    chunks: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, str]]]:
    """调用 DeepSeek 生成阶段相关摘要；失败时返回抽取式摘要。"""
    warnings: List[Dict[str, str]] = []
    if llm_service.has_api_key():
        excerpt = "\n\n".join(chunk["text"] for chunk in chunks)
        excerpt = excerpt[:SUMMARY_MAX_INPUT_CHARS]
        system_prompt = (
            "你是 APS 实施顾问的文档摘要助手。"
            "只根据给定文档片段提取事实，不编造数据。"
            "输出中文 Markdown，使用短标题和项目符号。"
        )
        user_message = f"""客户：{project.get('client_name') or '【未知】'}
当前阶段：阶段{stage} · {STAGE_BY_ID[stage]['name']}
文件名：{file_name}
本阶段交付物：{STAGE_BY_ID[stage]['deliverable']}

请提取与本阶段相关的结构化摘要。阶段一侧重排产周期、设备数量、产品族、插单记录；阶段二侧重数据对象、字段与来源；阶段三侧重约束与规则；阶段四侧重接口与测试；阶段五侧重上线范围与风险。
如果文档中没有某类信息，明确写“文档未提供”。

文档片段：
{excerpt}
"""
        try:
            return (
                llm_service.complete(
                    system_prompt,
                    [],
                    user_message,
                    max_tokens=SUMMARY_MAX_TOKENS,
                    temperature=0.2,
                ),
                warnings,
            )
        except (LlmNotConfigured, LlmCallError) as exc:
            log.warning("摘要生成失败，使用抽取式摘要：%s", exc)
            warnings.append(
                {
                    "code": "SUMMARY_FALLBACK",
                    "message": "DeepSeek 摘要生成失败，已使用抽取式摘要。",
                }
            )

    if not llm_service.has_api_key():
        warnings.append(
            {
                "code": "SUMMARY_FALLBACK",
                "message": "未配置 DEEPSEEK_API_KEY，已使用抽取式摘要。",
            }
        )

    return _extractive_summary(file_name, stage, chunks), warnings


# ---------------------------------------------------------------------------
# 上传主流程
# ---------------------------------------------------------------------------

def upload_document(
    project_id: str,
    stage: int,
    file_storage: Any,
) -> Dict[str, Any]:
    """上传并索引文档，返回 API 响应体（不含 ok）。"""
    project = context_store.get_project(project_id)
    if not project:
        raise ApiError(
            "PROJECT_NOT_FOUND",
            "项目不存在，请刷新页面或重新创建项目。",
            404,
        )
    project_deletion_service.ensure_not_deleting(project_id)
    if stage not in STAGE_IDS:
        raise ApiError("INVALID_STAGE", "阶段参数必须是 1-5。", 400)
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ApiError("NO_FILE", "请选择要上传的文件。", 400)

    original_name = Path(file_storage.filename).name.strip()
    if not original_name:
        raise ApiError("NO_FILE", "文件名为空。", 400)
    if len(original_name) > MAX_FILENAME_CHARS:
        raise ApiError("VALIDATION_ERROR", "文件名过长（最多 255 字符）。", 400)

    extension = Path(original_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ApiError(
            "UNSUPPORTED_FILE_TYPE",
            "仅支持 .docx、.xlsx、.pdf 文件。",
            415,
        )
    file_type = extension.lstrip(".")

    record = document_store.create_document(
        project_id=project_id,
        file_name=original_name,
        file_type=file_type,
        stage=stage,
        size_bytes=0,
        stored_path="",
    )
    doc_id = record["doc_id"]
    upload_dir = UPLOAD_DIR / project_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_path = upload_dir / f"{doc_id}{extension}"

    try:
        file_storage.save(str(stored_path))
        size_bytes = os.path.getsize(stored_path)
        if size_bytes > MAX_UPLOAD_BYTES:
            raise ApiError(
                "FILE_TOO_LARGE",
                f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB 限制。",
                413,
            )
        document_store.update_document(
            doc_id,
            stored_path=str(stored_path),
            size_bytes=size_bytes,
        )

        try:
            blocks = _parse_file(stored_path, file_type)
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("文档解析异常")
            raise ApiError(
                "PARSE_FAILED",
                f"文档解析失败：{type(exc).__name__}。",
                422,
            ) from exc

        chunks = _chunk_blocks(blocks)
        if not chunks:
            raise ApiError("EMPTY_DOCUMENT", "文档中没有可入库的文本。", 422)

        # 把来源元数据补到每个切片上，供内存索引过滤与 UI 展示。
        for chunk in chunks:
            chunk["metadata"] = {
                **chunk.get("metadata", {}),
                "file": original_name,
                "file_type": file_type,
                "stage": stage,
            }

        embeddings = None
        if embedding_service.is_configured():
            try:
                embeddings = embedding_service.embed_texts(
                    [chunk["text"] for chunk in chunks]
                )
            except ApiError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ApiError(
                    "EMBEDDING_FAILED",
                    f"外部 Embedding 调用失败：{type(exc).__name__}。",
                    502,
                ) from exc

        try:
            vector_store.add_chunks(
                project_id=project_id,
                doc_id=doc_id,
                chunks=chunks,
                embeddings=embeddings,
            )
        except vector_store.VectorStoreError as exc:
            raise ApiError("VECTOR_STORE_FAILED", str(exc), 500) from exc

        try:
            summary, warnings = _generate_summary(
                project, stage, original_name, chunks
            )
        except Exception as exc:  # noqa: BLE001 - 摘要失败不应阻断入库
            log.exception("摘要生成异常")
            summary = _extractive_summary(original_name, stage, chunks)
            warnings = [
                {
                    "code": "SUMMARY_FALLBACK",
                    "message": f"摘要生成异常（{type(exc).__name__}），已使用抽取式摘要。",
                }
            ]
        record = document_store.update_document(
            doc_id,
            status="indexed",
            chunks_count=len(chunks),
            summary=summary,
            indexed_at=document_store.now_iso(),
            error=None,
        )
        return {
            "doc_id": doc_id,
            "status": "indexed",
            "file": original_name,
            "file_type": file_type,
            "stage": stage,
            "chunks_count": len(chunks),
            "summary": summary,
            "summary_format": "markdown",
            "size_bytes": size_bytes,
            "created_at": record.get("created_at") if record else None,
            "indexed_at": record.get("indexed_at") if record else None,
            "warnings": warnings,
        }
    except ApiError as exc:
        document_store.mark_failed(doc_id, exc.code, exc.message)
        if exc.code in ("VECTOR_STORE_FAILED", "EMBEDDING_FAILED"):
            try:
                vector_store.delete_document(doc_id)
            except Exception:  # noqa: BLE001
                log.warning("清理失败文档切片时出错", exc_info=True)
        if not exc.data:
            exc.data = {"doc_id": doc_id}
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("文档入库失败")
        document_store.mark_failed(
            doc_id,
            "INTERNAL_ERROR",
            "文档入库失败，请查看服务日志。",
        )
        raise ApiError(
            "INTERNAL_ERROR",
            "文档入库失败，请稍后重试。",
            500,
            {"doc_id": doc_id},
        ) from exc


def list_documents(project_id: str) -> Dict[str, Any]:
    """返回项目文档列表。"""
    if not context_store.get_project(project_id):
        raise ApiError(
            "PROJECT_NOT_FOUND",
            "项目不存在，请刷新页面或重新创建项目。",
            404,
        )
    records = document_store.list_documents(project_id)
    documents = [document_store.public_document(record) for record in records]
    return {"total": len(documents), "documents": documents}
