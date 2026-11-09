# -*- coding: utf-8 -*-
"""领域异常与稳定错误码。

路由层只负责把 ApiError 转换为统一 JSON；服务层可以抛出带 code 的业务异常。
"""

from __future__ import annotations

from typing import Any, Optional


class ApiError(Exception):
    """可安全返回给前端的业务异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        data: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        """返回给前端的标准错误结构，error 字段兼容旧前端。"""
        return {
            "ok": False,
            "code": self.code,
            "message": self.message,
            "data": self.data,
            "error": self.message,
        }


class LlmNotConfigured(Exception):
    """未配置 DEEPSEEK_API_KEY，由上层决定是否使用演示回复。"""


class LlmCallError(Exception):
    """DeepSeek 调用失败，携带稳定错误码和 HTTP 状态。"""

    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_api_error(self) -> ApiError:
        return ApiError(self.code, self.message, self.status)
