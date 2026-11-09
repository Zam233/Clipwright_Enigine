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


def _payload(tmp_path) -> dict:
    media = tmp_path / "scene.mov"
    media.write_bytes(b"\x00")
    return {
        "path": str(media), "mime": "video/quicktime",
        "width": 1920, "height": 1080, "fps": 30.0, "duration": 2.0,
        "alpha": True, "artifact_id": "art_1",
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
    host = FakeHost(_payload(tmp_path))
    with pytest.raises(PluginRenderError) as err:
        await materialize_plugin_clips(
            _timeline("no_tool"), host=host, scope=RenderScope("demo", "proj-1"),
            width=1920, height=1080, fps=30.0, probe=_probe_ok,
        )
    assert err.value.code == "NO_RENDERER"


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
        payload = _payload(tmp_path) | {"mime": "text/plain"}
        with pytest.raises(PluginRenderError) as err:
            validate_result(payload, {"width": 1920, "height": 1080, "fps": 30.0,
                                      "duration_sec": 2.0, "start_sec": 0.0},
                            probe=_probe_ok)
        assert err.value.code == "BAD_MIME"

    def test_rejects_missing_alpha(self, tmp_path):
        with pytest.raises(PluginRenderError) as err:
            validate_result(_payload(tmp_path),
                            {"width": 1920, "height": 1080, "fps": 30.0,
                             "duration_sec": 2.0, "start_sec": 0.0},
                            probe=lambda p: {**_probe_ok(p), "pix_fmt": "yuv420p"})
        assert err.value.code == "NO_ALPHA"

    def test_rejects_size_mismatch(self, tmp_path):
        payload = _payload(tmp_path) | {"width": 1280}
        with pytest.raises(PluginRenderError) as err:
            validate_result(payload, {"width": 1920, "height": 1080, "fps": 30.0,
                                      "duration_sec": 2.0, "start_sec": 0.0},
                            probe=_probe_ok)
        assert err.value.code == "SIZE_MISMATCH"
