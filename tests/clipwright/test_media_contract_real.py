"""M5 真实媒体契约：用真实 ffmpeg 生成透明/非透明视频，跑真 ffprobe 校验。

没有 ffmpeg 的机器整组跳过（CI 不该依赖本机编码器）。
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from clipwright.services.plugin_render import (
    PluginRenderError,
    validate_result,
)
from clipwright.services.plugin_workspace import (
    PluginArtifactRegistry,
    PluginScope,
    PluginWorkspace,
)

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="需要 ffmpeg/ffprobe",
)

REQUEST = {
    "width": 320, "height": 240, "fps": 25.0,
    "duration_sec": 2.0, "start_sec": 0.0,
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    scope = PluginScope("demo", "proj-1", task_id="t1")
    ws = PluginWorkspace(scope)
    ws.root.mkdir(parents=True, exist_ok=True)
    return ws


def _encode(path, pix_fmt: str, codec: str) -> None:
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=red@0.5:s=320x240:d=2:r=25",
        "-c:v", codec, "-pix_fmt", pix_fmt, str(path),
    ]
    if codec == "prores_ks":
        cmd += ["-profile:v", "4444"]
    subprocess.run(cmd, check=True, capture_output=True)


def test_transparent_video_passes(workspace):
    target = workspace.root / "renders" / "scene.mov"
    target.parent.mkdir(parents=True, exist_ok=True)
    _encode(target, "yuva444p10le", "prores_ks")
    registry = PluginArtifactRegistry(workspace)
    entry = registry.register("renders/scene.mov", role="render")

    from clipwright.services.plugin_render import _resolve_payload_path

    resolved = _resolve_payload_path(
        {"artifact_id": entry["artifact_id"], "mime": "video/quicktime"},
        workspace.scope,
    )
    assert resolved["path"].endswith("scene.mov")

    # 用真实路径校验（真 ffprobe）
    overlay2 = validate_result(
        {**resolved, "width": 320, "height": 240, "fps": 25.0, "duration": 2.0},
        REQUEST,
    )
    assert overlay2["alpha"] is True
    assert "a" in overlay2["pix_fmt"]


def test_opaque_video_rejected(workspace):
    target = workspace.root / "renders" / "opaque.mov"
    target.parent.mkdir(parents=True, exist_ok=True)
    _encode(target, "yuv420p", "libx264")
    registry = PluginArtifactRegistry(workspace)
    entry = registry.register("renders/opaque.mov", role="render")

    with pytest.raises(PluginRenderError) as err:
        validate_result(
            {"artifact_id": entry["artifact_id"], "mime": "video/quicktime",
             "width": 320, "height": 240, "fps": 25.0, "duration": 2.0},
            REQUEST,
        )
    # artifact_id 未解析路径 → ARTIFACT_MISSING；解析后应为 NO_ALPHA
    assert err.value.code in {"ARTIFACT_MISSING", "NO_ALPHA"}
