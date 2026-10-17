"""trim 路径真实 ffmpeg 回归：临时输出名必须保留媒体扩展名。

背景（2026-09-05 引入的回归，2026-09-13 定位）
--------------------------------------------
原子替换改造把临时输出名写成 ``out + ".part"``（即 ``trim_x.mp4.part``）。
ffmpeg 只按输出文件后缀推断 muxer，遇到 ``.part`` 直接失败：
    Error initializing the muxer for ... / Error opening output files: Invalid argument
后果是**每一次裁剪都失败**，`_generate_fallback` 被调用，成片里所有素材被替换成
fallback 板（`scripts/e2e_smoke.py` 的 `fallback=3` 即此）。

为什么既有测试没拦住：`test_render_parallel.py` 等把 ffmpeg 边界整体 mock 掉了，
只验证并行调度；容器/扩展名这类"真实 ffmpeg 才知道"的错误没有任何缝隙被发现。
本文件补上真实 ffmpeg 的集成缝隙。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from clipwright.services.render import RenderService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="需要本机 ffmpeg/ffprobe",
)


def _synth(path: Path, seconds: float = 2.0) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-400:]


class TestAtomicTempPath:
    def test_keeps_media_extension_last(self) -> None:
        # 用 Path 比较，避免 Windows 下分隔符规范化差异
        assert Path(RenderService._atomic_temp_path("/tmp/trim_ab.mp4")).name == "trim_ab.part.mp4"
        assert Path(RenderService._atomic_temp_path("C:/w/mg_x.mov")).name == "mg_x.part.mov"
        assert Path(RenderService._atomic_temp_path("/tmp/noext")).name == "noext.part"

    def test_old_naming_would_break_ffmpeg(self, tmp_path: Path) -> None:
        """把旧命名（x.mp4.part）与修法（x.part.mp4）放到真实 ffmpeg 前对照。"""
        src = tmp_path / "in.mp4"
        _synth(src)
        bad = tmp_path / "out.mp4.part"
        good = tmp_path / "out.part.mp4"
        common = ["-y", "-loglevel", "error", "-i", str(src),
                  "-c:v", "libx264", "-b:v", "5M", "-an"]
        r_bad = subprocess.run(["ffmpeg", *common, str(bad)], capture_output=True, text=True)
        r_good = subprocess.run(["ffmpeg", *common, str(good)], capture_output=True, text=True)
        assert r_bad.returncode != 0 and not bad.exists(), "旧命名竟然成功了？"
        assert r_good.returncode == 0 and good.exists()


class TestTrimNoFallback:
    def test_trim_succeeds_without_fallback(self, tmp_path: Path) -> None:
        """真实裁剪一个合成源：不得发生 fallback，产物可解码且无 .part 残留。"""
        src = tmp_path / "src.mp4"
        _synth(src, seconds=2.0)
        svc = RenderService(work_dir=tmp_path / "engine")
        seg = {
            "source_path": str(src), "start_sec": 0.0, "duration_sec": 1.5,
            "source_offset": 0.0, "speed": 1.0, "metadata": {}, "keyframes": [],
        }

        out = asyncio.run(svc._trim_segments_parallel(
            [seg], 320, 240, 30.0, "5M",
            svc._current_encoder() if hasattr(svc, "_current_encoder") else "libx264",
            "veryfast", None,
        ))
        assert svc._fallback_count == 0, (
            "裁剪发生了 fallback（疑似临时输出名/muxer 推断问题）: "
            + " | ".join(svc._final_ffmpeg_log[-2:])
        )
        assert len(out) == 1, f"裁剪结果为空: {out}"
        p = Path(out[0])
        assert p.exists() and p.suffix in (".mp4", ".mov", ".mkv")
        assert ".part" not in p.name
        # 临时文件已被原子替换消费掉
        leftovers = list(p.parent.glob("*.part*"))
        assert not leftovers, f"残留临时文件: {leftovers}"
