# -*- coding: utf-8 -*-
"""应用配置：路径、模型、检索与上传限制。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
# 本地开发读取项目根目录 .env；已有环境变量优先（Docker / 终端 export）。
load_dotenv(BASE_DIR / ".env", override=False)


def _env_str(name: str, default: str) -> str:
    """空字符串也视为未设置，避免 .env 中留空导致路径变成当前目录。"""
    return (os.getenv(name) or "").strip() or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


# 运行数据目录：本地默认项目目录；Docker 通过 APS_DATA_DIR=/data 挂载持久卷。
DATA_DIR = Path(_env_str("APS_DATA_DIR", str(BASE_DIR)))

# 持久化路径
PROJECTS_FILE = DATA_DIR / "projects.json"
STORAGE_DIR = DATA_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
DOCUMENTS_FILE = STORAGE_DIR / "documents.json"
ASSISTANT_HISTORY_FILE = STORAGE_DIR / "assistant_history.json"
DELETIONS_DIR = STORAGE_DIR / "deletions"
CHROMA_DIR = Path(_env_str("CHROMA_DIR", str(STORAGE_DIR / "chroma")))

# DeepSeek
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = _env_str("DEEPSEEK_MODEL", "deepseek-chat")

# 对话与归档限制
MAX_USER_INPUT_CHARS = 8_000
MAX_ARCHIVE_CHARS = 200_000
MAX_ARCHIVE_CONTEXT_CHARS = 4_000
MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_CHARS = 30_000
ASSISTANT_MAX_HISTORY_MESSAGES = 40

# RAG
# 默认使用国内可访问的 Hugging Face 镜像；如需官方源可显式设置
# HF_ENDPOINT=https://huggingface.co。
HF_ENDPOINT = _env_str("HF_ENDPOINT", "https://hf-mirror.com")
HF_CACHE_DIR = Path(_env_str("HF_HOME", str(STORAGE_DIR / "hf-cache")))
EMBEDDING_MODEL = _env_str("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "aps_documents")
RAG_TOP_K = _env_int("RAG_TOP_K", 5)
RAG_MIN_SCORE = _env_float("RAG_MIN_SCORE", 0.35)
# 没有关键词命中时，仅接受高于该阈值的语义结果，避免弱相关片段被当成答案。
RAG_RELEVANCE_SCORE = _env_float("RAG_RELEVANCE_SCORE", 0.50)
CHUNK_TARGET_CHARS = 800
CHUNK_MAX_CHARS = 900
CHUNK_OVERLAP_CHARS = 100

# 上传
MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 20)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
# multipart 边界和表单字段会占用少量空间，为 Flask 总请求体上限预留 2 MiB。
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 2 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".docx", ".xlsx", ".pdf"}
MAX_FILENAME_CHARS = 255

# 上传摘要生成
SUMMARY_MAX_INPUT_CHARS = 6_000
SUMMARY_MAX_TOKENS = 900

# 状态
STAGE_IDS = (1, 2, 3, 4, 5)
STATUS_LABELS = {
    "not_started": "未开始",
    "in_progress": "进行中",
    "archived": "已归档",
}
