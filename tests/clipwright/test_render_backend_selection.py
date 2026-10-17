"""M5 测试：渲染后端选择接线（B-3 入口）。"""
from __future__ import annotations

import pytest

from clipwright.api import render as render_api


class TestRenderBackendSelection:
    def test_default_selects_local(self, monkeypatch):
        from clipwright.config import settings

        monkeypatch.setattr(settings, "renderer_backend", "local")
        monkeypatch.setattr(settings, "remote_render_url", "")
        svc = render_api._new_render_service()
        from clipwright.services.render import RenderService
        assert isinstance(svc, RenderService)

    def test_explicit_remote_setting(self, monkeypatch):
        from clipwright.config import settings

        monkeypatch.setattr(settings, "renderer_backend", "remote")
        monkeypatch.setattr(settings, "remote_render_url", "")
        svc = render_api._new_render_service()
        from clipwright.services.remote_render import RemoteRenderService
        assert isinstance(svc, RemoteRenderService)

    def test_legacy_remote_url_still_wins(self, monkeypatch):
        """既有行为不变：未显式设置 renderer_backend 时 remote_render_url 生效。"""
        from clipwright.config import settings

        monkeypatch.setattr(settings, "renderer_backend", "local")
        monkeypatch.setattr(settings, "remote_render_url", "http://worker:9000")
        svc = render_api._new_render_service()
        from clipwright.services.remote_render import RemoteRenderService
        assert isinstance(svc, RemoteRenderService)

    def test_unknown_setting_falls_back_local(self, monkeypatch):
        from clipwright.config import settings

        monkeypatch.setattr(settings, "renderer_backend", "banana")
        monkeypatch.setattr(settings, "remote_render_url", "")
        svc = render_api._new_render_service()
        from clipwright.services.render import RenderService
        assert isinstance(svc, RenderService)


class TestAdaptersInRegistry:
    def test_render_backends_registered_for_entry(self):
        """入口选择依赖的注册表在无启动流程时也可解析（懒注册）。"""
        from clipwright.providers.render import render_backend_names

        assert "local" in render_backend_names()
