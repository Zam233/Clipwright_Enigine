"""M5：渲染时插件回调的契约与媒体校验（materialize 改写时间线）。"""
from __future__ import annotations

import pytest

from clipwright.schema.timeline import Clip, Timeline, Track
from clipwright.services.clip_kind_registry import ClipKindDefinition, ClipKindRegistry
from clipwright.services.plugin_render import (
    PluginRenderError,
    RenderScope,
    materialize_plugin_clips,
    validate_result,
)

KIND = "probe_scene"


@pytest.fixture(autouse=True)
def _workspace_in_tmp(tmp_path, monkeypatch):
    """把 Workspace 的目录根指向 tmp_path，让插件工作区路径可预测（B4 契约）。"""
    from types import SimpleNamespace

    import clipwright.services.plugin_workspace as pw

    class _Ws:
        def __init__(self, project_id: str) -> None:
            self.project_id = project_id

        def dirs(self):
            root = tmp_path / "workspaces" / self.project_id
            root.mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(root=root, materials=root / "materials")

    monkeypatch.setattr(pw, "Workspace", _Ws)
    yield


@pytest.fixture(autouse=True)
def registry():
    ClipKindRegistry.clear()
    ClipKindRegistry.register(ClipKindDefinition(
        kind_id=KIND, owner_plugin="demo",
        render_contract="media_overlay.v1",
        renderer_tool="probe_renderer",
    ))
    yield
    ClipKindRegistry.clear()


def _timeline(kind: str = KIND) -> Timeline:
    clip = Clip(
        id="c1", kind=kind, asset_id="", track_id="t1",
        start_sec=0.0, duration_sec=2.0,
    )
    return Timeline(
        width=1920, height=1080, fps=30.0,
        tracks=[Track(id="t1", kind=kind, index=1, clips=[clip])],
    )


class FakeHost:
    def __init__(self, output: dict):
        self.output = output
        self.calls: list[dict] = []

    async def invoke(self, method, params, timeout=0.0, scope=None):
        self.calls.append({"method": method, "params": params})
        if isinstance(self.output, Exception):
            raise self.output
        if isinstance(self.output, dict) and "success" in self.output:
            return self.output
        return {"success": True, "output": self.output}


def _probe_ok(path):
    return {"width": 1920, "height": 1080, "fps": 30.0, "duration": 2.0,
            "pix_fmt": "yuva420p"}


def _payload(
    tmp_path, *, plugin_id: str = "demo", project_id: str = "proj-1",
    absolute: bool = False,
) -> dict:
    """合法结果：媒体放在该插件工作区内，默认用相对路径返回（B4 契约）。"""
    root = tmp_path / "workspaces" / project_id / "plugins" / plugin_id
    root.mkdir(parents=True, exist_ok=True)
    media = root / "scene.mov"
    media.write_bytes(b"\x00")
    return {
        # 默认相对路径，验证 materialize 的工作区归属；
        # validate_result 独立测试时传 absolute=True（它只做 probe 契约）。
        "path": str(media) if absolute else "scene.mov",
        "mime": "video/quicktime",
        "width": 1920, "height": 1080, "fps": 30.0, "duration": 2.0,
        "alpha": True,
    }


@pytest.mark.asyncio
async def test_materialize_rewrites_clip_and_track(tmp_path):
    host = FakeHost(_payload(tmp_path))
    timeline = await materialize_plugin_clips(
        _timeline(), host=host, scope=RenderScope("demo", "proj-1", "t1"),
        width=1920, height=1080, fps=30.0, probe=_probe_ok,
    )
    clip = timeline.tracks[0].clips[0]
    assert clip.kind == "video"
    assert clip.asset_id.endswith("scene.mov")
    assert clip.metadata["alpha"] is True
    assert timeline.tracks[0].kind == "video"
    # 请求里不含插件私有工程文件，只带 clip metadata
    request = host.calls[0]["params"]["input"]["request"]
    assert request["contract"] == "media_overlay.v1"
    assert request["clip_kind"] == KIND


@pytest.mark.asyncio
async def test_builtin_timeline_untouched(tmp_path):
    host = FakeHost(_payload(tmp_path))
    timeline = _timeline("video")
    result = await materialize_plugin_clips(
        timeline, host=host, scope=RenderScope("demo", "proj-1"),
        width=1920, height=1080, fps=30.0, probe=_probe_ok,
    )
    assert host.calls == []
    assert result.tracks[0].clips[0].kind == "video"


@pytest.mark.asyncio
async def test_plugin_failure_propagates(tmp_path):
    host = FakeHost({"success": False, "error": "boom"})
    with pytest.raises(PluginRenderError) as err:
        await materialize_plugin_clips(
            _timeline(), host=host, scope=RenderScope("demo", "proj-1"),
            width=1920, height=1080, fps=30.0, probe=_probe_ok,
        )
    assert err.value.code == "PLUGIN_RENDER_FAILED"


@pytest.mark.asyncio
async def test_missing_renderer_tool(tmp_path):
    ClipKindRegistry.clear()
    ClipKindRegistry.register(ClipKindDefinition(kind_id="no_tool", owner_plugin="demo"))
    host = FakeHost(_payload(tmp_path, absolute=True))
    with pytest.raises(PluginRenderError) as err:
        await materialize_plugin_clips(
            _timeline("no_tool"), host=host, scope=RenderScope("demo", "proj-1"),
            width=1920, height=1080, fps=30.0, probe=_probe_ok,
        )
    assert err.value.code == "NO_RENDERER"


@pytest.mark.asyncio
async def test_materialize_uses_resolver_and_owner_plugin(tmp_path):
    """host 可以是 plugin_id→host 的解析函数；请求里的 plugin_id 取定义 owner。"""
    host = FakeHost(_payload(tmp_path, absolute=True))
    seen: list[str] = []

    def resolver(plugin_id: str):
        seen.append(plugin_id)
        return host

    timeline = await materialize_plugin_clips(
        _timeline(), host=resolver, scope=RenderScope("ignored", "proj-1"),
        width=1920, height=1080, fps=30.0, probe=_probe_ok,
    )
    assert seen == ["demo"]
    assert timeline.tracks[0].clips[0].kind == "video"


def test_api_kwargs_empty_for_builtin():
    from clipwright.api.render import _plugin_render_kwargs

    assert _plugin_render_kwargs(_timeline("video")) == {}


@pytest.mark.asyncio
async def test_render_requires_plugin_scope(tmp_path, monkeypatch):
    """RenderService 遇到插件片段但没给 host/scope 时，必须显式失败。"""
    from clipwright.services import render as render_mod

    monkeypatch.setattr(render_mod, "ffmpeg_available", lambda: (True, "ok"))
    service = render_mod.RenderService()
    result = await service.render(
        _timeline(), tmp_path / "out.mp4", width=1920, height=1080, fps=30.0,
    )
    assert result.success is False
    assert "插件片段" in str(result.error)


class TestValidate:
    def test_rejects_non_video(self, tmp_path):
        payload = _payload(tmp_path, absolute=True) | {"mime": "text/plain"}
        with pytest.raises(PluginRenderError) as err:
            validate_result(payload, {"width": 1920, "height": 1080, "fps": 30.0,
                                      "duration_sec": 2.0, "start_sec": 0.0},
                            probe=_probe_ok)
        assert err.value.code == "BAD_MIME"

    def test_rejects_missing_alpha(self, tmp_path):
        with pytest.raises(PluginRenderError) as err:
            validate_result(_payload(tmp_path, absolute=True),
                            {"width": 1920, "height": 1080, "fps": 30.0,
                             "duration_sec": 2.0, "start_sec": 0.0},
                            probe=lambda p: {**_probe_ok(p), "pix_fmt": "yuv420p"})
        assert err.value.code == "NO_ALPHA"

    def test_rejects_size_mismatch(self, tmp_path):
        """B4：用 probe 实测尺寸判定，插件自报 payload.width 不再能掩盖不一致。"""
        payload = _payload(tmp_path, absolute=True)
        with pytest.raises(PluginRenderError) as err:
            validate_result(payload, {"width": 1920, "height": 1080, "fps": 30.0,
                                      "duration_sec": 2.0, "start_sec": 0.0},
                            probe=lambda p: {**_probe_ok(p), "width": 1280,
                                             "height": 720})
        assert err.value.code == "SIZE_MISMATCH"

    def test_self_reported_metadata_does_not_override_probe(self, tmp_path):
        """自报宽高与实测一致时通过，即使 payload 里写了别值也不改变判定。"""
        payload = _payload(tmp_path, absolute=True) | {"width": 1280, "height": 720}
        result = validate_result(
            payload,
            {"width": 1920, "height": 1080, "fps": 30.0,
             "duration_sec": 2.0, "start_sec": 0.0},
            probe=_probe_ok,
        )
        assert result["pix_fmt"] == "yuva420p"
