# -*- coding: utf-8 -*-
"""页面路由：首页仪表盘与项目工作台。"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, url_for

from data import context_store


page_bp = Blueprint("page", __name__)


@page_bp.get("/")
def index():
    """首页仪表盘。"""
    return render_template("index.html")


@page_bp.get("/presales/<presales_id>")
def presales_page(presales_id: str):
    """售前快速评估独立页面。"""
    record = context_store.get_presales(presales_id)
    if not record:
        return redirect(url_for("page.index", presales_missing="1"))
    return render_template(
        "presales.html", presales_id=presales_id, record=record
    )


@page_bp.get("/workbench/<project_id>")
def workbench(project_id: str):
    """项目工作台；项目不存在时回到首页并带提示参数。"""
    if not context_store.get_project(project_id):
        return redirect(url_for("page.index", project_missing="1"))
    return render_template("workbench.html", project_id=project_id)
