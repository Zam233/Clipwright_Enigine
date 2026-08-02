"""FrameValidatorTool 真机测试 — 用 FFmpeg lavfi 生成黑/白/正常测试片段。

验证 blackdetect + signalstats 阈值分类（视频范围 YUV：黑=16、白=235）：
- 黑帧片段 → is_blank=True
- 白帧片段 → is_white=True
- 正常测试源（testsrc2）→ valid=True，三个标志均为 False
- 缺失路径 → valid=False，不抛异常
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clipwright.tool.frame_validator import FrameValidatorTool
from clipwright.tool.video import resolve_ffmpeg

_FFMPEG_TIMEOUT = 60
_CLIP_SIZE = "320x240"
_CLIP_RATE = "15"


def _make_clip(tmp_path: Path, name: str, lavfi: str) -> Path:
    """用 ffmpeg lavfi 生成 1s 测试片段（h264/yuv420p mp4）。"""
    out = tmp_path / name
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            lavfi,
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=_FFMPEG_TIMEOUT,
        check=True,
    )
    return out


@pytest.mark.asyncio
async def test_black_clip_flagged_blank(tmp_path: Path) -> None:
    clip = _make_clip(
        tmp_path, "black.mp4", f"color=c=black:size={_CLIP_SIZE}:rate={_CLIP_RATE}"
    )
    result = await FrameValidatorTool().execute(video_url=str(clip))
    out = result.output or {}
    assert out["valid"] is True
    assert out["is_blank"] is True


@pytest.mark.asyncio
async def test_white_clip_flagged_white(tmp_path: Path) -> None:
    clip = _make_clip(
        tmp_path, "white.mp4", f"color=c=white:size={_CLIP_SIZE}:rate={_CLIP_RATE}"
    )
    result = await FrameValidatorTool().execute(video_url=str(clip))
    out = result.output or {}
    assert out["valid"] is True
    assert out["is_white"] is True


@pytest.mark.asyncio
async def test_normal_clip_valid(tmp_path: Path) -> None:
    clip = _make_clip(
        tmp_path, "normal.mp4", f"testsrc2=size={_CLIP_SIZE}:rate={_CLIP_RATE}"
    )
    result = await FrameValidatorTool().execute(video_url=str(clip))
    out = result.output or {}
    assert out["valid"] is True
    assert out["is_blank"] is False
    assert out["is_white"] is False
    assert out["is_overexposed"] is False
    assert out["match_score"] == 1.0


@pytest.mark.asyncio
async def test_missing_path_returns_invalid() -> None:
    result = await FrameValidatorTool().execute(
        video_url="C:/does/not/exist/frame_validator.mp4"
    )
    out = result.output or {}
    assert out["valid"] is False
    assert "error" in out


def test_registry_resolves_real_frame_validator() -> None:
    from clipwright.tool.registry import ToolRegistry

    ToolRegistry.clear()
    from clipwright.tool import register_builtin_tools

    register_builtin_tools()
    tool = ToolRegistry.get("frame_validator")
    assert tool is not None
    assert type(tool).__module__ == "clipwright.tool.frame_validator"
