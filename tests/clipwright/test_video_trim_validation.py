"""VideoTrimTool 输出校验测试。

覆盖：
- 有效输入 → SUCCESS 且产物含视频流
- 损坏输入（过小文件）→ ERROR，且不会调用 ffmpeg
- 输入缺失 → ERROR
- ffmpeg exit 0 但产物是空容器（~258B）→ ERROR
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from clipwright.schema.tool import ToolStatus
from clipwright.tool.video import VideoTrimTool, resolve_ffmpeg, resolve_ffprobe


def _run(coro):
    return asyncio.run(coro)


def _ffmpeg_available() -> bool:
    try:
        subprocess.run([resolve_ffmpeg(), "-version"], capture_output=True, timeout=10)
        subprocess.run([resolve_ffprobe(), "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


requires_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe unavailable")


@requires_ffmpeg
def test_trim_success_valid_output(tmp_path: Path):
    src = tmp_path / "src.mp4"
    subprocess.run(
        [resolve_ffmpeg(), "-y", "-f", "lavfi", "-i",
         "color=c=blue:s=320x240:d=1", "-pix_fmt", "yuv420p", str(src)],
        capture_output=True, timeout=60, check=True,
    )
    out = tmp_path / "trimmed.mp4"
    tool = VideoTrimTool()
    result = _run(tool.execute(input_path=str(src), start_sec=0, duration_sec=0.5,
                               output_path=str(out)))
    assert result.status == ToolStatus.SUCCESS, result.error
    assert result.output_path == str(out)
    assert tool._validate_trim_output(str(out))


def test_trim_rejects_empty_container(tmp_path: Path):
    """258B 假文件作为输入 → 输入预检直接 ERROR，不调用 ffmpeg。"""
    src = tmp_path / "fake.mp4"
    src.write_bytes(b"\x00" * 258)
    tool = VideoTrimTool()
    with patch("clipwright.tool.video._ffmpeg") as mock_ffmpeg:
        result = _run(tool.execute(input_path=str(src), start_sec=0, duration_sec=1,
                                   output_path=str(tmp_path / "out.mp4")))
    assert result.status == ToolStatus.ERROR
    assert "source missing/corrupt" in (result.error or "")
    mock_ffmpeg.assert_not_called()


def test_trim_rejects_missing_input(tmp_path: Path):
    tool = VideoTrimTool()
    result = _run(tool.execute(input_path=str(tmp_path / "nonexistent.mp4"),
                               start_sec=0, duration_sec=1,
                               output_path=str(tmp_path / "out.mp4")))
    assert result.status == ToolStatus.ERROR
    assert "source missing/corrupt" in (result.error or "")


def test_trim_rejects_corrupt_output(tmp_path: Path):
    """ffmpeg exit 0 但产物为 258B 空容器 → 输出校验 ERROR。"""
    src = tmp_path / "src.mp4"
    src.write_bytes(b"\x00" * 4096)  # 通过输入大小预检
    out = tmp_path / "out.mp4"
    out.write_bytes(b"\x00" * 258)  # 空容器

    async def fake_ffmpeg(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    tool = VideoTrimTool()
    with patch("clipwright.tool.video._ffmpeg", side_effect=fake_ffmpeg):
        result = _run(tool.execute(input_path=str(src), start_sec=0, duration_sec=1,
                                   output_path=str(out)))
    assert result.status == ToolStatus.ERROR
    assert "trim output invalid" in (result.error or "")
    assert result.output_path == str(out)
