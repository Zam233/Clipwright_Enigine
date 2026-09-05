"""MG overlay 叠加时序修复 — 单元测试。

背景: Hyperframes 输出的 MOV 叠加到主视频时，覆盖层时间戳被
`setpts=PTS-STARTPTS` 归零，导致动画内容在 `enable='between(t,start,end)'`
窗口打开前就播完（EOF 后 pass），窗口内看不到动画。

修复: 窗口分支的 setpts 改为 `PTS-STARTPTS+{start}/TB`，把覆盖层时间戳
平移到窗口起点，动画才能在窗口内继续播放。默认参数 (start=0/dur=0)
保持旧行为 `[0:v][1:v]overlay=format=auto[vout]` 不变。

本文件:
- (a) 基线表征测试：修复前 filter 含 `setpts=PTS-STARTPTS`（无偏移）
- (b) 失败优先测试：修复后 filter 含 `setpts=PTS-STARTPTS+{start}/TB`
- (c) 默认参数保持 `overlay=format=auto` 路径
- 畸形输入 / 防挂起（subprocess 超时）/ 确定性 lavfi 真实 ffmpeg 集成
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from clipwright.animation.hyperframes_renderer import HyperframesRenderer

_FFMPEG_TIMEOUT = 120


def _mk_fake_run(calls, overlay_dur: str = "6"):
    """构造假 subprocess.run：记录调用，ffprobe 返回 overlay 时长。"""
    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        cmd = args[0]
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout=overlay_dur, stderr="")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    return fake_run


def _ffmpeg_call(calls):
    """从记录的调用里取出 ffmpeg overlay 命令（跳过 _resolve_encoder 的 -encoders 探测）。"""
    for (args, _kwargs) in calls:
        if args[0][0] == "ffmpeg" and "-filter_complex" in args[0]:
            return args[0]
    raise AssertionError("未记录到 ffmpeg 调用")


def _filter_complex(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


class TestOverlayFilterString:
    """render_overlay_on_video 构建的 filter_complex 字符串。"""

    def test_baseline_window_filter_uses_plain_setpts(self, monkeypatch) -> None:
        """(a) 基线表征：修复前窗口分支 setpts 归零时间戳（无 /TB 偏移）。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        ok = HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
            start_sec=265, duration_sec=6,
        )
        assert ok is True
        assert "setpts=PTS-STARTPTS" in _filter_complex(_ffmpeg_call(calls))

    def test_window_filter_shifts_timestamps_to_start(self, monkeypatch) -> None:
        """(b) 修复目标：窗口分支 setpts 须带 +{start}/TB 平移，动画在窗口内才可见。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        ok = HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
            start_sec=265, duration_sec=6,
        )
        assert ok is True
        assert "setpts=PTS-STARTPTS+265/TB" in _filter_complex(_ffmpeg_call(calls))

    def test_window_filter_shifts_fractional_start(self, monkeypatch) -> None:
        """窗口分支对小数 start 也应输出安全格式化偏移（如 +5.5/TB）。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
            start_sec=5.5, duration_sec=6,
        )
        assert "setpts=PTS-STARTPTS+5.5/TB" in _filter_complex(_ffmpeg_call(calls))

    def test_defaults_keep_auto_overlay(self, monkeypatch) -> None:
        """(c) 默认参数 start=0/dur=0 保持旧路径 overlay=format=auto，无 setpts。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        ok = HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
        )
        assert ok is True
        fc = _filter_complex(_ffmpeg_call(calls))
        assert fc == "[0:v][1:v]overlay=format=auto[vout]"
        assert "setpts" not in fc

    def test_window_filter_preserves_enable_and_eof_action(self, monkeypatch) -> None:
        """窗口分支保留 enable=between(t,start,end) 与 eof_action=pass。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
            start_sec=265, duration_sec=6,
        )
        fc = _filter_complex(_ffmpeg_call(calls))
        assert "enable='between(t,265,271)'" in fc
        assert "eof_action=pass" in fc
        assert "format=yuva420p" in fc

    def test_t_truncation_applied_when_overlay_longer(self, monkeypatch) -> None:
        """覆盖层超长时在 -i overlay 之前加 -t {dur} 截断（保留旧逻辑）。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls, overlay_dur="10"))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
            start_sec=265, duration_sec=6,
        )
        cmd = _ffmpeg_call(calls)
        first_i = cmd.index("-i")
        assert cmd[first_i + 1] == "base.mp4"
        second_i = cmd.index("-i", first_i + 1)
        assert cmd[second_i + 1] == "anim.mov"
        assert cmd[cmd.index("-t") + 1] == "6"
        assert cmd.index("-t") < second_i


class TestOverlayMalformed:
    """畸形输入：不崩溃，默认分支行为不变。"""

    def test_start_beyond_base_duration_does_not_crash(self, monkeypatch) -> None:
        """start 远超主视频时长（如 100000s）不应崩溃，仍返回 bool。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        ok = HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
            start_sec=100000, duration_sec=6,
        )
        assert ok is True
        assert "between(t,100000,100006)" in _filter_complex(_ffmpeg_call(calls))

    def test_negative_start_with_dur_uses_window_path(self, monkeypatch) -> None:
        """start<0 但 dur>0 走窗口分支，不崩溃，setpts 偏移保留负号。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        ok = HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
            start_sec=-5, duration_sec=10,
        )
        assert ok is True
        fc = _filter_complex(_ffmpeg_call(calls))
        assert "between(t,-5,5)" in fc
        assert "setpts=PTS-STARTPTS+-5/TB" in fc

    def test_negative_start_zero_dur_uses_default_path(self, monkeypatch) -> None:
        """start<0 且 dur=0 走默认全时长叠加分支（保持旧行为）。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        ok = HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
            start_sec=-5, duration_sec=0,
        )
        assert ok is True
        assert _filter_complex(_ffmpeg_call(calls)) == "[0:v][1:v]overlay=format=auto[vout]"


class TestOverlayHangSafety:
    """防挂起：所有 subprocess 调用必须带超时。"""

    def test_all_subprocess_calls_carry_timeout(self, monkeypatch) -> None:
        """ffmpeg/ffprobe 调用都必须携带 timeout，避免真实进程卡死测试。"""
        calls = []
        monkeypatch.setattr(subprocess, "run", _mk_fake_run(calls))
        import clipwright.services.render as _render_mod
        monkeypatch.setattr(_render_mod, "run_tracked_ff", _mk_fake_run(calls))
        HyperframesRenderer.render_overlay_on_video(
            "anim.mov", "base.mp4", "out.mp4",
            start_sec=5, duration_sec=6,
        )
        assert calls, "应有 subprocess.run 调用"
        for (_args, kwargs) in calls:
            assert kwargs.get("timeout") and kwargs["timeout"] > 0, (
                "subprocess 调用缺少超时"
            )


def _non_base_pixel_ratio(path: Path) -> float:
    """统计非蓝像素占比（蓝色基座帧 → 非蓝像素即动画像素）。

    蓝色基座 (color=c=blue) 解码后 RGB 约 (0,0,255)（±20 容差）。
    """
    import numpy as np
    from PIL import Image

    arr = np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    blue = (abs(r) <= 20) & (abs(g) <= 20) & (abs(b - 255) <= 20)
    non_blue = int((~blue).sum())
    return non_blue / (arr.shape[0] * arr.shape[1])


class TestOverlayRealFfmpeg:
    """真实 ffmpeg 集成：确定性 lavfi 源，验证叠加窗口内动画可见（核心证据）。

    短超时 + 确定性源 + 每次全新 tmp_path，避免挂起/抖动/陈旧产物。
    """

    _SIZE = "320x180"
    _RATE = "30"

    def _run_real(self, overlay, main, out, start, dur, timeout=_FFMPEG_TIMEOUT):
        """调用函数并拦截其 ffmpeg 命令，用短超时真实执行（防挂起快速失败）。

        R10 合并: render_overlay_on_video 走 run_tracked_ff（内部用 Popen），
        同时拦截 subprocess.run 与 run_tracked_ff 以覆盖两种调用路径。
        """
        real_run = subprocess.run
        captured = {}

        def fake_ff(cmd, capture_output=False, text=False, timeout=None, **kw):
            captured["cmd"] = cmd
            return real_run(
                cmd, capture_output=True, text=False, timeout=timeout or 120, check=True,
            )

        import clipwright.services.render as _render_mod
        orig_run = subprocess.run
        orig_rff = _render_mod.run_tracked_ff
        subprocess.run = fake_ff
        _render_mod.run_tracked_ff = fake_ff
        try:
            ok = HyperframesRenderer.render_overlay_on_video(
                str(overlay), str(main), str(out),
                start_sec=start, duration_sec=dur,
            )
        finally:
            subprocess.run = orig_run
            _render_mod.run_tracked_ff = orig_rff
        return ok, captured.get("cmd")

    def _make_lavfi(self, tmp_path, name, lavfi, duration, is_mov):
        out = tmp_path / name
        codec = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if is_mov:
            codec = ["-c:v", "mpeg4", "-q:v", "3"]
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", lavfi,
                "-t", str(duration),
                *codec,
                str(out),
            ],
            capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT, check=True,
        )
        return out

    def _frame(self, video, ts, out_png):
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(video),
                "-ss", str(ts), "-frames:v", "1",
                str(out_png),
            ],
            capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT, check=True,
        )
        return out_png

    def test_real_overlay_animation_visible_in_window(self, tmp_path) -> None:
        """窗口 [5,11] 内应看到动画像素（>0%），窗口外应为纯蓝（0% 动画像素）。"""
        base = self._make_lavfi(
            tmp_path, "base.mp4", f"color=c=blue:size={self._SIZE}:rate={self._RATE}",
            14, is_mov=False,
        )
        anim = self._make_lavfi(
            tmp_path, "anim.mov", f"testsrc2=size={self._SIZE}:rate={self._RATE}",
            6, is_mov=True,
        )
        out = tmp_path / "out.mp4"
        ok, cmd = self._run_real(anim, base, out, start=5, dur=6)
        assert ok is True
        assert cmd is not None
        assert "enable='between(t,5,11)'" in cmd[cmd.index("-filter_complex") + 1]

        in_png = self._frame(out, 7.5, tmp_path / "in_window.png")
        out_png = self._frame(out, 1.0, tmp_path / "out_window.png")
        in_ratio = _non_base_pixel_ratio(in_png)
        out_ratio = _non_base_pixel_ratio(out_png)
        assert in_ratio > 0.0, "窗口内应有动画像素（非蓝像素占比 > 0%）"
        assert out_ratio == 0.0, "窗口外应为纯蓝基座（0% 动画像素）"

    def test_real_overlay_defaults_plain_overlay(self, tmp_path) -> None:
        """默认参数走 overlay=format=auto 路径，动画全程可见。"""
        base = self._make_lavfi(
            tmp_path, "base.mp4", f"color=c=blue:size={self._SIZE}:rate={self._RATE}",
            6, is_mov=False,
        )
        anim = self._make_lavfi(
            tmp_path, "anim.mov", f"testsrc2=size={self._SIZE}:rate={self._RATE}",
            6, is_mov=True,
        )
        out = tmp_path / "out.mp4"
        ok, cmd = self._run_real(anim, base, out, start=0, dur=0)
        assert ok is True
        assert cmd is not None
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert fc == "[0:v][1:v]overlay=format=auto[vout]"

        png = self._frame(out, 3.0, tmp_path / "mid.png")
        assert _non_base_pixel_ratio(png) > 0.0, "默认路径动画应可见"
