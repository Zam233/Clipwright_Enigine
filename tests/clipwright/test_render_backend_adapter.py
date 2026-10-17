"""M5 测试：RendererBackend 适配器（B-3，打桩服务 + 协议符合性）。"""
from __future__ import annotations

import asyncio

import pytest

from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
from clipwright.providers.base import RendererBackend
from clipwright.providers.render import (
    LocalFFmpegBackend,
    RemoteRenderBackend,
    RenderBackendEntry,
    _cancel_id_of,
    ensure_render_backends_registered,
    render_backend_names,
    resolve_preferred_render_backend,
    resolve_render_backend,
)


class _FakeRenderService:
    """替身渲染服务：记录调用参数并返回伪结果。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def render(self, timeline, output_path, **kwargs):
        self.calls.append({"timeline": timeline, "output": output_path, **kwargs})
        return {"status": "ok", "output": output_path}


@pytest.fixture(autouse=True)
def _clean():
    yield
    ContributionRegistry.clear_all()


class TestProtocolConformance:
    def test_both_backends_satisfy_protocol(self):
        for backend in (LocalFFmpegBackend(service_factory=_FakeRenderService),
                        RemoteRenderBackend(service_factory=_FakeRenderService)):
            assert isinstance(backend, RendererBackend), f"{backend.name} 不符合协议"
            caps = backend.capabilities()
            assert caps["name"] == backend.name

    def test_registered_and_names(self):
        ensure_render_backends_registered()
        assert set(render_backend_names()) == {"local", "remote"}
        assert ContributionRegistry.owner_of(
            ContributionKind.RENDERER_BACKEND, "local") == "__builtin__"
        assert isinstance(resolve_render_backend("local"), LocalFFmpegBackend)
        assert isinstance(resolve_render_backend("remote"), RemoteRenderBackend)
        assert resolve_render_backend("nope") is None

    def test_preferred_resolution_defaults_local(self):
        backend = resolve_preferred_render_backend()
        assert backend is not None and backend.name == "local"


class TestSettingsMapping:
    def test_settings_dict_maps_to_service_kwargs(self):
        svc = _FakeRenderService()
        backend = LocalFFmpegBackend(service_factory=lambda: svc)
        progress: list = []
        result = asyncio.run(backend.render(
            {"tracks": []}, "out.mp4",
            {"width": 1080, "height": 1920, "fps": 24, "bitrate": "4M",
             "loudnorm_enabled": False, "not_a_real_key": 1},
            on_progress=lambda p, m: progress.append((p, m)),
            cancel_token="cancel-123",
        ))
        assert result["status"] == "ok"
        call = svc.calls[0]
        assert call["width"] == 1080 and call["height"] == 1920 and call["fps"] == 24
        assert call["bitrate"] == "4M" and call["loudnorm_enabled"] is False
        assert "not_a_real_key" not in call  # 白名单外的键不透传
        assert "progress_callback" in call and "cancel_id" in call
        assert call["cancel_id"] == "cancel-123"

    def test_cancel_token_mapping_variants(self):
        assert _cancel_id_of(None) is None
        assert _cancel_id_of("abc") == "abc"

        class _Token:
            cancel_id = "cid-9"

        assert _cancel_id_of(_Token()) == "cid-9"
        assert _cancel_id_of(object()) is None

    def test_remote_declares_limited_cancel(self):
        caps = RemoteRenderBackend(service_factory=_FakeRenderService).capabilities()
        assert caps["cancel"] == "limited"
        assert caps["supports_soft_subtitle"] is False
