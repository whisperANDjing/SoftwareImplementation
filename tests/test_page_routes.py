# -*- coding: utf-8 -*-
"""首页仪表盘与工作台页面路由测试。"""

from __future__ import annotations

from app import app


def test_dashboard_page_renders(store):
    response = app.test_client().get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-page="dashboard"' in html
    assert "项目总览" in html
    assert "dashboard.js" in html
    assert 'id="back-home-btn"' not in html


def test_workbench_page_requires_existing_project(store, project):
    response = app.test_client().get(f"/workbench/{project['id']}")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-page="workbench"' in html
    assert f'data-project-id="{project["id"]}"' in html
    assert 'id="back-home-btn"' in html
    assert 'id="leave-confirm"' in html


def test_missing_workbench_redirects_home(store):
    response = app.test_client().get("/workbench/prj_missing")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/?project_missing=1")
