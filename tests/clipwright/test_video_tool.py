"""VideoThumbnailTool 标题 drawtext 转义回归测试（Bug B7）。

覆盖：
- 含冒号 + CJK 的标题（如 "T3: 发布会"）→ SUCCESS 且产物可被 ffprobe 解析
- 含逗号标题（"A, B"）→ SUCCESS
- 含单引号标题（"It's"）→ SUCCESS
- 空标题 → 不抛异常
- 基线钉住：源码中逗号转义 "\\," 存在，防止未来被静默移除
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

import clipwright.tool.video as video_mod
from clipwright.schema.tool import ToolStatus
from clipwright.tool.video import VideoThumbnailTool, resolve_ffmpeg, resolve_ffprobe


def _run(coro):
    return asyncio.run(coro)


def _ffmpeg_available() -> bool:
    try:
        subprocess.run([resolve_ffmpeg(), "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


requires_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg/ffprobe unavailable"
)


def _input_video(tmp_path: Path) -> str:
    src = tmp_path / "src.mp4"
    subprocess.run(
        [resolve_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=red:s=320x240:d=1", "-pix_fmt", "yuv420p", str(src)],
        capture_output=True, timeout=60, check=True,
    )
    return str(src)


def _probe_valid(path: Path) -> bool:
    return subprocess.run(
        [resolve_ffprobe(), "-v", "error", str(path)],
        capture_output=True, timeout=30,
    ).returncode == 0


@requires_ffmpeg
@pytest.mark.parametrize("title", [
    "T3: 发布会",  # 冒号 + CJK
    "A, B",        # 逗号
    "It's",        # 单引号
])
def test_title_escaping_success(tmp_path: Path, title: str) -> None:
    src = _input_video(tmp_path)
    out = tmp_path / "out.jpg"
    result = _run(VideoThumbnailTool().execute(
        input_path=src, text=title, output_path=str(out)))
    assert result.status == ToolStatus.SUCCESS, result.error
    assert out.exists()
    assert _probe_valid(out), "产物无法被 ffprobe 解析"


@requires_ffmpeg
def test_empty_title_no_crash(tmp_path: Path) -> None:
    src = _input_video(tmp_path)
    out = tmp_path / "out.jpg"
    result = _run(VideoThumbnailTool().execute(
        input_path=src, text="", output_path=str(out)))
    assert result.status == ToolStatus.SUCCESS, result.error
    assert out.exists()


class TestBaselinePinned:
    """基线钉住：逗号转义表达式存在，防止未来被静默移除。"""

    def test_comma_escape_callsite_exists(self) -> None:
        src = Path(video_mod.__file__).read_text(encoding="utf-8")
        assert '.replace(",", "\\\\,")' in src, "缺少逗号转义 \\,"
