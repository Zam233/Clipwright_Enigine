"""`POST /api/animation/mg/html` — HTML 即时预览端点（P2-3）测试。

要点：
- 返回合成 HTML 本身（含 hf_timeline 运行时与 `window.__timelines[` 注册），不渲染；
- CLI 不可用时**仍可用**（这是相对渲染档的核心优势）；
- 非法输入返回 400/404，与渲染档一致；
- 与渲染档共用 `_resolve_preview_mg`，背景守卫（默认透明）保持一致。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipwright.animation.mg_renderer import MGRenderer
from clipwright.main import app

client = TestClient(app)
URL = "/api/animation/mg/html"


def _template_body() -> dict:
    """取一个真实内置模板（缺失则跳过，避免依赖运行时模板资源）。"""
    anims = MGRenderer.list_animations()
    if not anims:
        pytest.skip("无内置 MG 模板")
    return {"animation_id": anims[0]["id"], "width": 640, "height": 360, "fps": 30}


def test_html_preview_returns_composition_html() -> None:
    r = client.post(URL, json=_template_body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert "<!DOCTYPE html>" in data["html"]
    # 与主管线同源的运行时时间轴：前端 iframe 靠它 seek
    assert "window.__timelines[" in data["html"]
    assert "data-composition-id" in data["html"]
    assert data["duration_sec"] > 0
    assert data["width"] == 640 and data["height"] == 360 and data["fps"] == 30
    assert data["animation_id"]


def test_html_preview_accepts_inline_mg_json() -> None:
    mg_def = {
        "animation_id": "diag_inline",
        "duration_sec": 2.0,
        "style": {"background": "transparent", "font_family": "sans-serif"},
        "params": {"text": {"type": "string", "default": "T"}},
        "elements": [{
            "type": "text", "content": "{text}", "x": "center", "y": "center",
            "font_size": 48, "font_color": "#ffffff",
            "keyframes": [
                {"time": 0.0, "properties": {"opacity": 0}},
                {"time": 2.0, "properties": {"opacity": 1}},
            ],
        }],
    }
    r = client.post(URL, json={"mg_json": mg_def, "params": {"text": "即时预览"}, "width": 640, "height": 360})
    assert r.status_code == 200, r.text
    assert "即时预览" in r.json()["html"]


def test_html_preview_unknown_template_404() -> None:
    r = client.post(URL, json={"animation_id": "mg_does_not_exist_xyz"})
    assert r.status_code == 404


def test_html_preview_invalid_mg_json_400() -> None:
    r = client.post(URL, json={"mg_json": {"animation_id": "x", "elements": []}})
    assert r.status_code == 400


def test_html_preview_forces_transparent_background() -> None:
    """背景守卫与主管线一致：vision_prompt 为空时剥离不透明 bg 元素。"""
    mg_def = {
        "animation_id": "diag_bg",
        "duration_sec": 2.0,
        "style": {"background": "#123456", "font_family": "sans-serif"},
        "elements": [
            {"type": "bg", "background": "#000000", "keyframes": [
                {"time": 0.0, "properties": {"opacity": 1}},
                {"time": 2.0, "properties": {"opacity": 1}},
            ]},
            {"type": "text", "content": "T", "x": "center", "y": "center", "keyframes": [
                {"time": 0.0, "properties": {"opacity": 0}},
                {"time": 2.0, "properties": {"opacity": 1}},
            ]},
        ],
    }
    r = client.post(URL, json={"mg_json": mg_def})
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert "background:#000000" not in html.replace(" ", "").lower()
