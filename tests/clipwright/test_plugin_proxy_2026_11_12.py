"""W01：插件专用受限代理的服务端权限裁定回归。

- 禁用/未发现插件 → 拒绝（403/404）
- (plugin, method, action) 未命中服务端清单 → 403（不接受请求体自报路径）
- 白名单命中 → 转发到服务端映射的目标（普通宿主 API 不因代理放宽）
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from clipwright.api import plugin as pj


async def test_proxy_rejects_disabled_plugin(monkeypatch):
    class _Loader:
        @staticmethod
        def discover():
            return ["ai_image_gen"]

        @staticmethod
        def is_enabled(pid):
            return False

    monkeypatch.setattr(pj, "_loader", _Loader())
    with pytest.raises(HTTPException) as ei:
        await pj.plugin_proxy("ai_image_gen", "post",
                        pj._PluginProxyBody(action="tool", payload={}), None)
    assert ei.value.status_code == 403
    assert "已禁用" in ei.value.detail


async def test_proxy_rejects_unknown_plugin(monkeypatch):
    class _Loader:
        @staticmethod
        def discover():
            return []

        @staticmethod
        def is_enabled(pid):
            return False

    monkeypatch.setattr(pj, "_loader", _Loader())
    with pytest.raises(HTTPException) as ei:
        await pj.plugin_proxy("evil", "post",
                        pj._PluginProxyBody(action="tool", payload={}), None)
    assert ei.value.status_code == 404


async def test_proxy_rejects_action_not_in_allowlist(monkeypatch):
    """插件存在且启用，但 action 未在服务端清单 → 403；不接受任意自报路径。"""
    class _Loader:
        @staticmethod
        def discover():
            return ["ai_image_gen"]

        @staticmethod
        def is_enabled(pid):
            return True

    monkeypatch.setattr(pj, "_loader", _Loader())
    with pytest.raises(HTTPException) as ei:
        await pj.plugin_proxy("ai_image_gen", "post",
                        pj._PluginProxyBody(action="delete_project", payload={}), None)
    assert ei.value.status_code == 403


async def test_proxy_rejects_get_for_post_only_action(monkeypatch):
    class _Loader:
        @staticmethod
        def discover():
            return ["ai_image_gen"]

        @staticmethod
        def is_enabled(pid):
            return True

    monkeypatch.setattr(pj, "_loader", _Loader())
    with pytest.raises(HTTPException) as ei:
        await pj.plugin_proxy("ai_image_gen", "get",
                        pj._PluginProxyBody(action="tool", payload={}), None)
    assert ei.value.status_code == 403


async def test_proxy_rejects_unsupported_method():
    with pytest.raises(HTTPException) as ei:
        await pj.plugin_proxy("ai_image_gen", "delete",
                        pj._PluginProxyBody(action="tool", payload={}), None)
    assert ei.value.status_code == 405


def test_allowlist_has_no_destructive_targets():
    """清单目标必须是既有只读/工具执行/安装触发端点，禁止 DELETE/PUT 形态。"""
    allowed_targets = {
        "/api/tool/execute", "/api/render/browser", "/api/render/browser/install",
    }
    for pid, actions in pj._PLUGIN_PROXY_ALLOWED.items():
        for key, target in actions.items():
            method, action = key.split(":", 1)
            assert method in ("GET", "POST"), f"{pid}:{key}"
            assert target.startswith("/api/"), f"{pid}:{key}"
            assert target in allowed_targets, f"{pid}:{key}"


def test_proxy_body_rejects_path_field():
    """请求体没有 path/endpoint 字段——插件不能指定任意目标。"""
    fields = pj._PluginProxyBody.model_fields
    assert "path" not in fields
    assert "endpoint" not in fields
    assert set(fields) == {"action", "payload"}
