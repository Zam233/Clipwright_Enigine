"""P0/P1 回归：export_preset 纯数据贡献必须注册成功且带前端契约字段。

来自 2026-11-08 的插件/engine 边界审查：
- `example_full` 的 export_preset 在 T8.5 收紧后仍是旧形状（object="preset"、数据在
  payload 里），被 dispatch 静默拒绝——插件"加载成功"但贡献丢了；
- `platform_export` 的预设把数据放在 meta 顶层，缺 `title`/`payload`，前端
  `usePluginContributions('export_preset')` 按契约会整条丢弃。
"""

from __future__ import annotations

import pytest

from clipwright.plugins.contribution import ContributionKind, ContributionRegistry


@pytest.fixture(autouse=True)
def _clean():
    yield
    ContributionRegistry.clear_all()


def _items(plugin_cls):
    return plugin_cls().contributions()


def test_example_full_export_preset_is_accepted():
    from plugins.example_full.main import ExampleFullPlugin

    items = _items(ExampleFullPlugin)
    ContributionRegistry.declared_scope("example_full_test", [c.key for c in items])
    report = ContributionRegistry.register("example_full_test", items)
    assert report.ok, report.rejected

    item = ContributionRegistry.resolve(ContributionKind.EXPORT_PRESET, "example_preset")
    assert item is not None, "example_full 的 export_preset 未被注册（被静默拒绝）"
    assert item.object is None
    meta = item.meta
    assert isinstance(meta.get("title"), str) and meta["title"]
    for key in ("width", "height", "fps"):
        assert isinstance(meta[key], int) and meta[key] > 0, key
    assert str(meta.get("bitrate") or "")


def test_platform_export_presets_carry_frontend_contract():
    from plugins.platform_export.main import PlatformExportPlugin

    items = _items(PlatformExportPlugin)
    ContributionRegistry.declared_scope("platform_export_test", [c.key for c in items])
    report = ContributionRegistry.register("platform_export_test", items)
    assert report.ok, report.rejected

    presets = [
        c for c in items if c.kind is ContributionKind.EXPORT_PRESET
    ]
    assert len(presets) >= 6
    for c in presets:
        meta = c.meta
        # 前端契约：title 字符串 + payload 带宽高（或兼容地从顶层读）
        assert isinstance(meta.get("title"), str) and meta["title"], c.id
        payload = meta.get("payload")
        assert isinstance(payload, dict), c.id
        assert payload.get("width") and payload.get("height"), c.id
        # 注册期校验仍读顶层
        assert meta.get("width") and meta.get("height") and meta.get("fps"), c.id


def test_provider_available_includes_display_meta():
    """P2：/api/providers/available 必须带 provider_meta（VoicePage 的 label/提示来源）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipwright.api import providers as providers_api
    from plugins.voice_ext.main import VoiceExtPlugin

    items = _items(VoiceExtPlugin)
    ContributionRegistry.declared_scope("voice_ext_meta_test", [c.key for c in items])
    report = ContributionRegistry.register("voice_ext_meta_test", items)
    assert report.ok, report.rejected

    app = FastAPI()
    app.include_router(providers_api.router)
    body = TestClient(app).get("/api/providers/available").json()
    meta = body["provider_meta"]["tts_provider"]
    assert meta["elevenlabs"]["name"] == "ElevenLabs"
    assert meta["elevenlabs"]["needs_uploader"] is False
    assert meta["azure"]["name"] == "Azure Speech"
