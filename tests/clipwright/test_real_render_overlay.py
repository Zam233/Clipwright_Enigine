"""M5 真渲染：底层视频 + 插件 kind overlay，走真实 ffmpeg 合成。

没有 ffmpeg 的机器整组跳过。测试只跑 320x240 / 2s / 25fps，避免拖慢 CI。
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from clipwright.schema.timeline import Clip, Timeline, Track
from clipwright.services.clip_kind_registry import ClipKindDefinition, ClipKindRegistry
from clipwright.services.plugin_workspace import (
    PluginArtifactRegistry,
    PluginScope,
    PluginWorkspace,
)
from clipwright.services.render import RenderService

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="需要 ffmpeg/ffprobe",
)

KIND = "real_probe_scene"


class _Host:
    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        self.alive = True

    async def invoke(self, method, params, timeout=0.0, scope=None):
        return {"success": True, "output": {
            "artifact_id": self.artifact_id,
            "mime": "video/quicktime",
            "width": 320, "height": 240, "fps": 25.0, "duration": 2.0,
            "alpha": True,
        }}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    monkeypatch.setattr(config.settings, "render_output_dir", str(tmp_path / "renders"), raising=False)
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)

    # 底层视频
    base = tmp_path / "base.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", "color=c=blue:s=320x240:d=2:r=25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(base),
    ], check=True, capture_output=True)

    # 透明 overlay（prores 4444）
    scope = PluginScope("demo", "proj-1", task_id="t1")
    ws = PluginWorkspace(scope)
    target = ws.root / "renders" / "scene.mov"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", "color=c=red@0.5:s=320x240:d=2:r=25",
        "-c:v", "prores_ks", "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le", str(target),
    ], check=True, capture_output=True)
    artifact = PluginArtifactRegistry(ws).register("renders/scene.mov", role="render")

    ClipKindRegistry.clear()
    ClipKindRegistry.register(ClipKindDefinition(
        kind_id=KIND, owner_plugin="demo",
        render_contract="media_overlay.v1", renderer_tool="probe_render",
    ))
    yield tmp_path, base, artifact["artifact_id"]
    ClipKindRegistry.clear()


@pytest.mark.asyncio
async def test_render_composites_plugin_overlay(setup):
    tmp_path, base, artifact_id = setup
    timeline = Timeline(width=320, height=240, fps=25.0, duration_sec=2.0, tracks=[
        Track(id="t0", kind="video", index=0, clips=[Clip(
            id="c0", kind="video", asset_id=str(base), track_id="t0",
            start_sec=0.0, duration_sec=2.0,
        )]),
        Track(id="t1", kind=KIND, index=1, clips=[Clip(
            id="c1", kind=KIND, asset_id="", track_id="t1",
            start_sec=0.0, duration_sec=2.0,
        )]),
    ])

    out = tmp_path / "out.mp4"
    result = await RenderService().render(
        timeline, out,
        width=320, height=240, fps=25.0, bitrate="1M",
        plugin_host=lambda _pid: _Host(artifact_id),
        plugin_scope={"project_id": "proj-1", "task_id": "t1"},
    )
    assert result.success, result.error
    assert out.is_file() and out.stat().st_size > 0
