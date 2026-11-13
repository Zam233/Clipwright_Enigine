"""C1：图解渲染器不得在插件卸载后仍从缓存命中。"""

from __future__ import annotations

from clipwright.animation.diagram_svg import DiagramRenderer
from clipwright.plugins.hooks import HookPoint, HookRegistry


def _register_plugin_renderer(plugin_id: str, preset: str) -> None:
    def _renderer(items, text, style, width, height):
        return f"<svg data-preset='{preset}'>{text}</svg>"

    def _hook(_ctx):
        return {"renderers": [{"id": preset, "name": preset, "renderer": _renderer}]}

    HookRegistry.register(HookPoint.DIAGRAM_RENDERER_EXTEND, _hook, plugin_id=plugin_id)


def test_hook_renderer_works_then_disappears_after_unload():
    preset = "plugin_radar_probe"
    _register_plugin_renderer("logic_probe", preset)
    try:
        html = DiagramRenderer.render({"preset": preset, "title": "t", "items": []})
        assert f"data-preset='{preset}'" in html
        # 目录里也应能看到该 preset
        ids = {p["id"] for p in DiagramRenderer.get_supported_presets()}
        assert preset in ids
    finally:
        HookRegistry.unregister_plugin("logic_probe")

    # 卸载后不得再命中旧渲染器，也不能残留在目录里
    fallback = DiagramRenderer.render({"preset": preset, "title": "t", "items": []})
    assert f"data-preset='{preset}'" not in fallback
    ids_after = {p["id"] for p in DiagramRenderer.get_supported_presets()}
    assert preset not in ids_after


def test_register_renderer_public_api_still_works():
    """公共 API 显式注册的渲染器应保留（不受 Hook 缓存移除影响）。"""
    name = "public_probe"

    def _fn(items, text, style, width, height):
        return f"<svg data-public='{name}'></svg>"

    DiagramRenderer.register_renderer(name, _fn)
    try:
        html = DiagramRenderer.render({"preset": name, "title": "", "items": []})
        assert f"data-public='{name}'" in html
    finally:
        DiagramRenderer._custom_renderers.pop(name, None)
