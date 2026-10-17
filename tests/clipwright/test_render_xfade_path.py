"""xfade 转场路径回归（真实 ffmpeg）。

背景：`_xfade_pair` 收到的 out_name 形如 `xr1_ab12cd.{ext}`，旧实现直接
`self._work_dir / out_name` —— 输出文件名带字面量 `{ext}`，ffmpeg 无法推断 muxer
→ 每次都失败 → `_final_ffmpeg_log` 记「输出无效，降级硬切拼接」→ **转场从未进入成片**，
连带让转场漂移校正把字幕/音频错误前移（成片不是被转场缩短的，却被按缩短量前移）。

本文件锁定：
- xfade 产物确实生成且时长 = 两段之和 − 转场时长；
- 有转场的时间线渲染后，成片时长 ≈ 时间线时长 − 转场累计侵蚀（时长校验不误报）。
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


def _clip(path: Path, seconds: float) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-300:]


def _duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except (TypeError, ValueError):
        return -1.0


class TestXfadePair:
    def test_output_name_is_formatted_and_xfade_applies(self, tmp_path: Path) -> None:
        """`{ext}` 必须被替换成真实扩展名，且产物时长 = 左+右 − 转场。"""
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        _clip(a, 2.0)
        _clip(b, 2.0)
        svc = RenderService(work_dir=tmp_path / "engine")
        (tmp_path / "engine").mkdir(parents=True, exist_ok=True)

        out = svc._xfade_pair(str(a), str(b), "fade", 0.5, 30.0, "5M", "libx264", "veryfast",
                              "xr1_deadbe.{ext}")

        assert "{ext}" not in out, f"输出名未格式化: {out}"
        assert Path(out).exists(), "xfade 未产出文件（疑似 muxer 推断失败）"
        assert Path(out).suffix == ".mp4"
        # 2 + 2 - 0.5 = 3.5s
        assert abs(_duration(out) - 3.5) < 0.25, f"xfade 时长异常: {_duration(out)}"
        assert not any("降级硬切拼接" in m for m in svc._final_ffmpeg_log)

    def test_illegal_transition_falls_back_without_crash(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        _clip(a, 1.0)
        _clip(b, 1.0)
        (tmp_path / "engine").mkdir(parents=True, exist_ok=True)
        svc = RenderService(work_dir=tmp_path / "engine")
        out = svc._xfade_pair(str(a), str(b), "not_a_transition", 0.4, 30.0, "5M",
                              "libx264", "veryfast", "xr2_beef.{ext}")
        # 非法转场名 → ffmpeg 失败 → 回退硬切（保留可播放产物，不抛异常）
        assert Path(out).exists()


class TestTimelineWithTransitions:
    def test_render_duration_matches_expected_with_transitions(self, tmp_path: Path) -> None:
        """3 段 5s + 2 处 0.5s 转场 → 成片 ≈ 14s，且不再触发时长告警。"""
        from clipwright.schema.timeline import Timeline

        clips = []
        for i in (1, 2, 3):
            p = tmp_path / f"m{i}.mp4"
            _clip(p, 5.0)
            clip = {
                "id": f"c{i}", "kind": "video", "asset_id": str(p), "track_id": "v0",
                "start_sec": (i - 1) * 5.0, "duration_sec": 5.0, "source_offset_sec": 0,
                "speed": 1, "volume": 0, "opacity": 1, "keyframes": [], "metadata": {},
            }
            if i > 1:
                clip["transition_in"] = "fade"
                clip["transition_duration_sec"] = 0.5
            clips.append(clip)
        tl = Timeline(**{
            "id": "xfade_case", "width": 320, "height": 240, "fps": 30, "duration_sec": 15,
            "tracks": [{"id": "v0", "name": "视频", "kind": "video", "index": 0, "clips": clips}],
        })
        svc = RenderService(work_dir=tmp_path / "engine")
        res = asyncio.run(svc.render(tl, tmp_path / "out.mp4", width=320, height=240, fps=30))
        assert res.success, res.error
        assert abs(res.duration_sec - 14.0) < 0.4, f"成片时长应为 ≈14s，实际 {res.duration_sec}"
        assert not any("时长偏差" in w for w in res.warnings), res.warnings
