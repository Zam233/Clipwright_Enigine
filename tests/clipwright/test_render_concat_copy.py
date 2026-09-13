"""V1 规则 2 回归：无转场时的无损拼接（concat demuxer + `-c copy`）。

背景：裁剪阶段已把每个片段编码成最终参数，旧实现仍在拼接阶段用
`concat` **滤镜**再编码一遍（trim 一次 + concat 一次 = 每段两代编码损失，
且整片要重编一次）。本文件锁定：
- 参数一致的片段走 `-c copy` 拼接（产物名 `concat_copy.*`，时长 = 各段之和）；
- 参数不一致 / copy 失败 / 开关关闭 → 回退 filter 拼接（产物名 `concat.*`，行为不退化）；
- 有转场时**不**走 copy 路径（转场必须重编码）。
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


def _clip(path: Path, seconds: float, size: str = "320x240", fps: int = 30) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-300:]


def _duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip())


def _svc(tmp_path: Path) -> RenderService:
    return RenderService(work_dir=tmp_path / "engine")


class TestVideoSignature:
    def test_signature_of_valid_clip(self, tmp_path: Path) -> None:
        p = tmp_path / "a.mp4"
        _clip(p, 1.0)
        sig = RenderService._video_signature(str(p))
        assert sig is not None
        codec, pix_fmt, w, h, fps, sar = sig
        assert codec == "h264" and pix_fmt == "yuv420p" and (w, h) == (320, 240)
        assert fps == "30/1"

    def test_signature_of_missing_file(self) -> None:
        assert RenderService._video_signature("does_not_exist.mp4") is None


class TestConcatCopy:
    def test_copy_path_used_for_matching_clips(self, tmp_path: Path) -> None:
        svc = _svc(tmp_path)
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        _clip(a, 1.0)
        _clip(b, 1.5)
        out = svc._try_concat_copy([str(a), str(b)])
        assert out is not None and Path(out).name.startswith("concat_copy")
        assert Path(out).exists()
        total = _duration(out)
        assert abs(total - 2.5) < 0.2, f"时长应≈两段之和，实际 {total}"
        assert any("无损拼接" in m for m in svc._final_ffmpeg_log)

    def test_falls_back_when_parameters_differ(self, tmp_path: Path) -> None:
        svc = _svc(tmp_path)
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        _clip(a, 1.0, size="320x240")
        _clip(b, 1.0, size="640x480")  # 分辨率不同 → 不可 copy 拼接
        assert svc._try_concat_copy([str(a), str(b)]) is None
        assert any("参数不一致" in m for m in svc._final_ffmpeg_log)

    def test_kill_switch(self, tmp_path: Path, monkeypatch) -> None:
        svc = _svc(tmp_path)
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        _clip(a, 1.0)
        _clip(b, 1.0)
        monkeypatch.setenv("CLIPWRIGHT_CONCAT_COPY", "0")
        assert svc._try_concat_copy([str(a), str(b)]) is None

    def test_single_input_is_noop(self, tmp_path: Path) -> None:
        svc = _svc(tmp_path)
        a = tmp_path / "a.mp4"
        _clip(a, 1.0)
        assert svc._try_concat_copy([str(a)]) is None

    def test_relative_paths_are_resolved(self) -> None:
        """真实管线传给拼接的是**相对 CWD** 的 trim 缓存路径（_cache/tmp/trim_cache/…）。

        ffconcat 的相对路径按**列表文件所在目录**解析，若不转绝对路径会直接
        "No such file or directory" → 无损拼接静默退化为重编码。本用例锁定该回归。
        """
        import os

        rel_dir = Path("_cache") / "tmp" / "concat_copy_test"
        rel_dir.mkdir(parents=True, exist_ok=True)
        try:
            a = rel_dir / "a.mp4"
            b = rel_dir / "b.mp4"
            _clip(a, 0.8)
            _clip(b, 0.8)
            rel_paths = [os.path.relpath(a, Path.cwd()), os.path.relpath(b, Path.cwd())]
            assert not Path(rel_paths[0]).is_absolute(), "用例前提：路径应为相对路径"
            svc = RenderService(work_dir=rel_dir / "engine")
            out = svc._try_concat_copy(rel_paths)
            assert out is not None and Path(out).exists(), (
                "相对路径未被解析为绝对路径: " + " | ".join(svc._final_ffmpeg_log[-2:])
            )
            assert abs(_duration(out) - 1.6) < 0.2
        finally:
            shutil.rmtree(rel_dir, ignore_errors=True)


class TestConcatSegmentsIntegration:
    def _run(self, svc: RenderService, trimmed: list[str], segments: list[dict],
             *, has_transition: bool) -> str:
        return asyncio.run(svc._concat_segments(
            trimmed, segments, 30.0, "5M", "libx264", "veryfast", None,
            width=320, height=240,
        ))

    def test_no_transition_uses_lossless_concat(self, tmp_path: Path) -> None:
        svc = _svc(tmp_path)
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        _clip(a, 1.0)
        _clip(b, 1.0)
        segs = [
            {"start_sec": 0.0, "duration_sec": 1.0},
            {"start_sec": 1.0, "duration_sec": 1.0},
        ]
        out = self._run(svc, [str(a), str(b)], segs, has_transition=False)
        assert Path(out).name.startswith("concat_copy"), f"应走无损拼接，实际 {out}"
        assert abs(_duration(out) - 2.0) < 0.2

    def test_transition_forces_reencode_path(self, tmp_path: Path) -> None:
        svc = _svc(tmp_path)
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        _clip(a, 1.0)
        _clip(b, 1.0)
        segs = [
            {"start_sec": 0.0, "duration_sec": 1.0},
            {"start_sec": 1.0, "duration_sec": 1.0, "transition_in": "fade",
             "transition_duration_sec": 0.3},
        ]
        out = self._run(svc, [str(a), str(b)], segs, has_transition=True)
        # 有转场必须重编码：产物名不是 concat_copy（xfade 或 filter 拼接产物）
        assert not Path(out).name.startswith("concat_copy"), f"转场不应走 copy 路径: {out}"
        assert Path(out).exists()

    def test_mismatch_falls_back_to_filter_concat(self, tmp_path: Path) -> None:
        svc = _svc(tmp_path)
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        _clip(a, 1.0, size="320x240")
        _clip(b, 1.0, size="640x480")
        segs = [
            {"start_sec": 0.0, "duration_sec": 1.0},
            {"start_sec": 1.0, "duration_sec": 1.0},
        ]
        out = self._run(svc, [str(a), str(b)], segs, has_transition=False)
        assert Path(out).exists(), "回退路径必须仍产出成片"
        assert any("参数不一致" in m for m in svc._final_ffmpeg_log)
