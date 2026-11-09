# -*- coding: utf-8 -*-
"""APS 实施顾问 AI 工作台应用入口。

只负责创建 Flask 应用、注册 Blueprint 和统一错误处理；
业务逻辑位于 services/，数据读写位于 data/。
"""

from __future__ import annotations

import logging

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from config import MAX_UPLOAD_MB
from errors import ApiError
from routes.assistant_routes import assistant_bp
from routes.page_routes import page_bp
from routes.post_launch_routes import post_launch_bp
from routes.presales_routes import presales_bp
from routes.project_routes import project_bp
from routes.rag_routes import rag_bp
from runtime_config import get_runtime_config
from services import observability, project_deletion_service


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("aps-workbench")


def create_app() -> Flask:
    runtime_config = get_runtime_config()
    app = Flask(__name__)
    app.config.from_object(runtime_config)
    app.config["MAX_CONTENT_LENGTH"] = runtime_config.MAX_CONTENT_LENGTH
    app.config["RUNTIME_ENV"] = runtime_config.ENV_NAME
    app.json.ensure_ascii = False

    app.register_blueprint(page_bp)
    app.register_blueprint(project_bp)
    app.register_blueprint(presales_bp)
    app.register_blueprint(post_launch_bp)
    app.register_blueprint(rag_bp)
    app.register_blueprint(assistant_bp)

    try:
        project_deletion_service.recover_pending_deletions()
    except Exception:  # noqa: BLE001 - 恢复失败不应阻止应用启动
        log.exception("项目删除日志恢复失败")

    @app.before_request
    def before_request():
        """记录请求开始时间、请求 ID 并重置 token 统计。"""
        observability.start_request()

    @app.after_request
    def after_request(response):
        """注入请求 ID；非流式 API 在这里记录耗时和 token。"""
        response.headers.setdefault("X-Request-ID", getattr(g, "request_id", ""))
        if request.path.startswith("/api/") and not getattr(
            g, "streaming_response", False
        ):
            observability.log_request(response.status_code)
        return response

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return jsonify(error.to_dict()), error.status

    @app.errorhandler(RequestEntityTooLarge)
    def handle_file_too_large(_error: RequestEntityTooLarge):
        message = f"文件超过 {MAX_UPLOAD_MB} MiB 限制，请压缩或拆分后上传。"
        return (
            jsonify(
                {
                    "ok": False,
                    "code": "FILE_TOO_LARGE",
                    "message": message,
                    "data": None,
                    "error": message,
                }
            ),
            413,
        )

    @app.errorhandler(404)
    def handle_not_found(_error):
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "code": "ROUTE_NOT_FOUND",
                        "message": "接口不存在。",
                        "data": None,
                        "error": "接口不存在。",
                    }
                ),
                404,
            )
        return "页面不存在", 404

    @app.errorhandler(405)
    def handle_method_not_allowed(_error):
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "code": "METHOD_NOT_ALLOWED",
                        "message": "HTTP 方法不允许。",
                        "data": None,
                        "error": "HTTP 方法不允许。",
                    }
                ),
                405,
            )
        return "HTTP 方法不允许", 405

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        if isinstance(error, HTTPException):
            return error
        log.exception("未处理异常")
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "code": "INTERNAL_ERROR",
                        "message": "服务器内部错误，请稍后重试。",
                        "data": None,
                        "error": "服务器内部错误，请稍后重试。",
                    }
                ),
                500,
            )
        return "服务器内部错误", 500

    return app


app = create_app()


if __name__ == "__main__":
    runtime_config = get_runtime_config()
    log.info(
        "APS 工作台启动：env=%s http://%s:%s",
        runtime_config.ENV_NAME,
        runtime_config.HOST,
        runtime_config.PORT,
    )
    app.run(
        host=runtime_config.HOST,
        port=runtime_config.PORT,
        debug=runtime_config.DEBUG,
    )
