"""P2-2 收尾：渲染后端经统一贡献注册表选择（含插件贡献的 sidecar 后端）。

背景：`providers/render/` 早已注册 local/remote 两个内建后端，但渲染入口
（`api/render.py::_new_render_service`）原先只认 "local"/"remote" 两个字符串 +
`remote_render_url`，**插件贡献的渲染后端选不中**——这正是「@hyperframes/producer
常驻 sidecar 插件化」落地的最后一环。本轮把入口改走注册表，并补发现端点。

覆盖：
- `renderer_backend` 指定内建 remote / 插件贡献后端 / 未知名回退；
- 插件后端只实现 `RendererBackend.render(timeline, output, settings, on_progress)`
  协议时，经 `_BackendServiceAdapter` 也能被既有调用方直接使用（kwargs 收口）；
- `/api/render/backends` 列出内建 + 贡献后端、能力声明与可用性、生效后端；
- 缺省行为不变（未配置 → 本机；配了 remote_render_url → remote）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.providers.render import (
    LocalFFmpegBackend,
    RemoteRenderBackend,
    ensure_render_backends_registered,
    render_backend_names,
    resolve_preferred_render_backend,
    resolve_render_backend,
)

CONTRIBUTOR = "plugin:producer-test"


class _FakeProducerBackend:
    """模拟插件贡献的 sidecar 后端（只实现协议，不实现服务 API）。"""

    name = "producer"

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str, dict, Any]] = []

    def capabilities(self) -> dict:
        return {"name": self.name, "supports_gpu": True, "sidecar": "@hyperframes/producer"}

    async def probe(self) -> bool:
        return True

    async def render(self, timeline, output_path, settings, on_progress=None, **extra):  # noqa: ANN001
        self.calls.append((timeline, output_path, settings, on_progress))
        if on_progress:
            on_progress(50.0, "producer 渲染中")
        return {"success": True, "output_path": output_path, "duration_sec": 1.0,
                "warnings": [], "produced_by": self.name}


@pytest.fixture()
def producer_plugin():
    """注册一个贡献名为 producer 的渲染后端（模拟第三方插件），测试后清理。"""
    backend = _FakeProducerBackend()
    ensure_render_backends_registered()
    ContributionRegistry.register(CONTRIBUTOR, [
        Contribution(ContributionKind.RENDERER_BACKEND, "producer", backend),
    ], dispatch=False)
    try:
        yield backend
    finally:
        try:
            ContributionRegistry.unregister_owner(CONTRIBUTOR)
        except Exception:  # noqa: BLE001 - 老接口无 unregister_owner 时忽略
            pass


class TestRegistryResolution:
    def test_builtin_backends_registered(self) -> None:
        names = render_backend_names()
        assert "local" in names and "remote" in names

    def test_resolve_builtins(self) -> None:
        assert isinstance(resolve_render_backend("local"), LocalFFmpegBackend)
        assert isinstance(resolve_render_backend("remote"), RemoteRenderBackend)

    def test_plugin_backend_resolves_as_object(self, producer_plugin) -> None:
        assert resolve_render_backend("producer") is producer_plugin
        assert "producer" in render_backend_names()

    def test_unknown_backend_is_none(self) -> None:
        assert resolve_render_backend("nope") is None

    def test_preferred_falls_back_to_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clipwright.config import settings

        monkeypatch.setattr(settings, "renderer_backend", "nope")
        assert resolve_preferred_render_backend().name == "local"

    def test_preferred_returns_plugin_backend(self, monkeypatch: pytest.MonkeyPatch, producer_plugin) -> None:
        from clipwright.config import settings

        monkeypatch.setattr(settings, "renderer_backend", "producer")
        assert resolve_preferred_render_backend() is producer_plugin


class TestNewRenderService:
    """入口选择：注册表命中就用，未命中回退本机（缺省行为不变）。"""

    def _service(self, monkeypatch: pytest.MonkeyPatch, backend: str, remote_url: str = ""):
        from clipwright.api import render as render_api
        from clipwright.config import settings

        monkeypatch.setattr(settings, "renderer_backend", backend)
        monkeypatch.setattr(settings, "remote_render_url", remote_url)
        return render_api._new_render_service()

    def test_default_is_local_render_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clipwright.services.render import RenderService

        assert isinstance(self._service(monkeypatch, "local"), RenderService)

    def test_builtin_remote_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clipwright.services.remote_render import RemoteRenderService

        assert isinstance(self._service(monkeypatch, "remote"), RemoteRenderService)

    def test_plugin_backend_selected_via_adapter(self, monkeypatch: pytest.MonkeyPatch, producer_plugin) -> None:
        from clipwright.api.render import _BackendServiceAdapter

        service = self._service(monkeypatch, "producer")
        assert isinstance(service, _BackendServiceAdapter)
        assert service.name == "producer"

    def test_plugin_backend_render_translates_kwargs(self, monkeypatch: pytest.MonkeyPatch, producer_plugin) -> None:
        service = self._service(monkeypatch, "producer")
        progress: list[tuple[float, str]] = []
        result = asyncio.run(service.render(
            timeline={"tracks": []}, output_path="out.mp4",
            width=1920, height=1080, fps=30, progress_callback=lambda p, m: progress.append((p, m)),
        ))
        assert result["success"] is True
        _timeline, output, settings_dict, on_progress = producer_plugin.calls[0]
        assert output == "out.mp4"
        assert settings_dict["width"] == 1920 and settings_dict["fps"] == 30
        # progress_callback 被映射成后端的 on_progress，且不再留在 settings 里
        assert "progress_callback" not in settings_dict
        assert on_progress is not None
        assert progress == [(50.0, "producer 渲染中")]

    def test_unknown_backend_falls_back_to_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clipwright.services.render import RenderService

        assert isinstance(self._service(monkeypatch, "nope"), RenderService)

    def test_remote_url_still_selects_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clipwright.services.remote_render import RemoteRenderService

        assert isinstance(
            self._service(monkeypatch, "local", remote_url="http://worker:9000"),
            RemoteRenderService,
        )

    def test_object_without_service_or_render_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """贡献对象既不是服务也没实现 render() 时不能抛错，回退本机。"""
        from clipwright.api.render import _backend_as_service
        from clipwright.services.render import RenderService

        assert isinstance(_backend_as_service(object()), RenderService)


class TestBackendsEndpoint:
    def _client(self, monkeypatch: pytest.MonkeyPatch, backend: str = "local"):
        from fastapi.testclient import TestClient

        from clipwright.config import settings
        from clipwright.main import app

        monkeypatch.setattr(settings, "renderer_backend", backend)
        ensure_render_backends_registered()
        return TestClient(app)

    def test_lists_builtin_backends_with_capabilities(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(monkeypatch)
        resp = client.get("/api/render/backends")
        assert resp.status_code == 200
        body = resp.json()
        names = {b["name"] for b in body["backends"]}
        assert {"local", "remote"} <= names
        local = next(b for b in body["backends"] if b["name"] == "local")
        assert local["capabilities"]["name"] == "local"
        assert isinstance(local["available"], bool)
        assert body["requested"] == "local"
        assert body["effective"] == "local"

    def test_lists_plugin_contributed_backend(self, monkeypatch: pytest.MonkeyPatch, producer_plugin) -> None:
        client = self._client(monkeypatch, backend="producer")
        body = client.get("/api/render/backends").json()
        entry = next((b for b in body["backends"] if b["name"] == "producer"), None)
        assert entry is not None
        assert entry["capabilities"]["sidecar"] == "@hyperframes/producer"
        assert entry["available"] is True
        assert body["effective"] == "producer"

    def test_unknown_requested_still_reports_effective_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(monkeypatch, backend="nope")
        body = client.get("/api/render/backends").json()
        assert body["requested"] == "nope"
        assert body["effective"] == "local"  # 回退后的真实生效后端
