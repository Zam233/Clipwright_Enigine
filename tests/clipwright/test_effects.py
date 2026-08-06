"""Bug B1/B2/B3/B5/B6 回归测试 — 效果/转场/模糊工具的有效 filter 串。

本地 ffmpeg 8.1.2 确认：xfade 支持 rectcrop / circleopen / fade 等，
不支持 glitch / rectangle / clock；``mblur`` filter 已不存在；
``colorchannelmixer`` 需用命名参数（rr=/rg=/rb=...），位置式 ``.393*.769*...`` 无效。

Failing-first（修复前，以下断言应失败）：
- B1: transition='glitch' 命中无效 ``xfade=transition=glitch`` → ffmpeg ERROR
- B2: transition='rect' → ``xfade=transition=rectangle`` 无效 → ERROR
- B3: transition='clock' → ``xfade=transition=clock`` 无效 → ERROR
- B5: blur_type='motion' → ``mblur`` 不存在 → ERROR
- B6: effect='sepia' → 位置式 colorchannelmixer 无效 → ERROR
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from clipwright.schema.tool import ToolStatus
from clipwright.tool.effects import (
    EffectVignetteTool,
    TransitionApplyTool,
    VideoBlurTool,
)


def _run(coro):
    return asyncio.run(coro)


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


requires_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe unavailable")


def _tiny_clip(path: Path, color: str = "red") -> str:
    """用真实 ffmpeg 生成 0.5s 测试视频。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=320x240:d=0.5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=60)
    return str(path)


def _probe_ok(path: str) -> bool:
    """ffprobe 能正常解析输出文件 → 有效容器。"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, timeout=30)
    return result.returncode == 0


def _source_text() -> str:
    """读取 effects.py 源码文本用于 filter 串断言。"""
    import clipwright.tool.effects as effects_mod
    return Path(effects_mod.__file__).read_text(encoding="utf-8")


class TestTransitionB1Glitch:
    """B1: 'glitch' 键被移除 → 回退到 crossfade（transition=fade）。"""

    @requires_ffmpeg
    def test_glitch_runs_and_falls_back_to_crossfade(self, tmp_path: Path) -> None:
        a = _tiny_clip(tmp_path / "a.mp4", color="red")
        b = _tiny_clip(tmp_path / "b.mp4", color="blue")
        out = tmp_path / "out.mp4"
        tool = TransitionApplyTool()
        result = _run(tool.execute(clip_a=a, clip_b=b, transition="glitch",
                                   output_path=str(out)))
        assert result.status == ToolStatus.SUCCESS, result.error
        assert result.output_path == str(out)
        assert _probe_ok(str(out))

    def test_glitch_key_removed(self) -> None:
        src = _source_text()
        assert '"glitch":' not in src, "B1: glitch 键应被移除，回退到 crossfade"


class TestTransitionB2Rect:
    """B2: 'rect' → xfade=transition=rectcrop。"""

    @requires_ffmpeg
    def test_rect_runs_with_real_ffmpeg(self, tmp_path: Path) -> None:
        a = _tiny_clip(tmp_path / "a.mp4", color="red")
        b = _tiny_clip(tmp_path / "b.mp4", color="blue")
        out = tmp_path / "out.mp4"
        tool = TransitionApplyTool()
        result = _run(tool.execute(clip_a=a, clip_b=b, transition="rect",
                                   output_path=str(out)))
        assert result.status == ToolStatus.SUCCESS, result.error
        assert _probe_ok(str(out))

    def test_rect_filter_uses_rectcrop(self) -> None:
        src = _source_text()
        assert "transition=rectcrop" in src, "B2: rect 应映射到 rectcrop"
        assert "transition=rectangle" not in src, "B2: 无效的 rectangle 应移除"


class TestTransitionB3Clock:
    """B3: 'clock' → xfade=transition=circleopen。"""

    @requires_ffmpeg
    def test_clock_runs_with_real_ffmpeg(self, tmp_path: Path) -> None:
        a = _tiny_clip(tmp_path / "a.mp4", color="red")
        b = _tiny_clip(tmp_path / "b.mp4", color="blue")
        out = tmp_path / "out.mp4"
        tool = TransitionApplyTool()
        result = _run(tool.execute(clip_a=a, clip_b=b, transition="clock",
                                   output_path=str(out)))
        assert result.status == ToolStatus.SUCCESS, result.error
        assert _probe_ok(str(out))

    def test_clock_filter_uses_circleopen(self) -> None:
        src = _source_text()
        assert "transition=circleopen" in src, "B3: clock 应映射到 circleopen"
        assert "transition=clock" not in src, "B3: 无效的 clock 应移除"


class TestBlurB5Motion:
    """B5: 'motion' 用 gblur=sigma 替代不存在的 mblur。"""

    @requires_ffmpeg
    def test_motion_runs_with_real_ffmpeg(self, tmp_path: Path) -> None:
        src = _tiny_clip(tmp_path / "src.mp4", color="red")
        out = tmp_path / "out.mp4"
        tool = VideoBlurTool()
        result = _run(tool.execute(input_path=src, blur_type="motion",
                                   radius=5, output_path=str(out)))
        assert result.status == ToolStatus.SUCCESS, result.error
        assert _probe_ok(str(out))

    @requires_ffmpeg
    def test_invalid_blur_type_falls_back_to_gaussian(self, tmp_path: Path) -> None:
        src = _tiny_clip(tmp_path / "src.mp4", color="red")
        out = tmp_path / "out.mp4"
        tool = VideoBlurTool()
        result = _run(tool.execute(input_path=src, blur_type="nonexistent",
                                   radius=5, output_path=str(out)))
        assert result.status == ToolStatus.SUCCESS, result.error
        assert _probe_ok(str(out))

    def test_motion_uses_gblur_not_mblur(self) -> None:
        src = _source_text()
        assert 'f"gblur=sigma={radius}"' in src, "B5: motion 应映射到 gblur=sigma"
        assert "mblur" not in src, "B5: 不存在的 mblur filter 应移除"


class TestVignetteB6Sepia:
    """B6: 'sepia' 用命名参数 colorchannelmixer=rr=... 替代位置式。"""

    @requires_ffmpeg
    def test_sepia_runs_with_real_ffmpeg(self, tmp_path: Path) -> None:
        src = _tiny_clip(tmp_path / "src.mp4", color="red")
        out = tmp_path / "out.mp4"
        tool = EffectVignetteTool()
        result = _run(tool.execute(input_path=src, effect="sepia",
                                   output_path=str(out)))
        assert result.status == ToolStatus.SUCCESS, result.error
        assert _probe_ok(str(out))

    @requires_ffmpeg
    def test_old_film_runs_with_real_ffmpeg(self, tmp_path: Path) -> None:
        src = _tiny_clip(tmp_path / "src.mp4", color="red")
        out = tmp_path / "out.mp4"
        tool = EffectVignetteTool()
        result = _run(tool.execute(input_path=src, effect="old_film",
                                   output_path=str(out)))
        assert result.status == ToolStatus.SUCCESS, result.error
        assert _probe_ok(str(out))

    def test_sepia_uses_named_colorchannelmixer_args(self) -> None:
        src = _source_text()
        assert "rr=0.393" in src, "B6: sepia 应使用 colorchannelmixer 命名参数"
        assert "colorchannelmixer=.393" not in src, "B6: 位置式 colorchannelmixer 应移除"
