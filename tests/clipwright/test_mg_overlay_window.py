"""Bug2 回归测试 — MG overlay 时间窗口修复。

基线（Baseline）：钉住无时间窗口时 overlay filter 的原始形态。
Failing-first：传入 start_sec/duration_sec 后，overlay filter 必须包含
``enable='between(t,{start},{start+dur})'``，使每个 MG MOV 只在自己的时段合成。
若修复缺失，该测试会失败。

断言对象是真实传给 ffmpeg 的 ``-filter_complex`` 字符串（可观察行为）。
"""

from __future__ import annotations

import re
import subprocess

import clipwright.services.render as render_mod
from clipwright.animation.hyperframes_renderer import HyperframesRenderer
from clipwright.services.render import RenderService


def _capture_ffmpeg(monkeypatch) -> list[list[str]]:
    """拦截 render_overlay_on_video 内部的真实 subprocess.run，记录命令行。"""
    cmds: list[list[str]] = []

    def fake_run(cmd, **kw):
        cmds.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    # 固定编码器，避免测试机器差异影响断言
    monkeypatch.setattr(render_mod, "_resolve_encoder", lambda: "libx264")
    return cmds


def _filter_complex(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


class TestBaselinePinned:
    """基线特征化测试：钉住当前（已修复）行为。"""

    def test_no_window_plain_overlay(self, monkeypatch) -> None:
        """不传时间窗口 → 纯 overlay=format=auto，无 enable（基线行为）。"""
        cmds = _capture_ffmpeg(monkeypatch)
        ok = HyperframesRenderer.render_overlay_on_video("ov.mov", "main.mp4", "out.mp4")
        assert ok is True
        assert _filter_complex(cmds[0]) == "[0:v][1:v]overlay=format=auto[vout]"


class TestMGOverlayTimeWindow:
    """Failing-first：overlay 必须带 enable 时间窗口。"""

    def test_overlay_has_enable_window(self, monkeypatch) -> None:
        """start=12.5, dur=3.0 → enable='between(t,12.5,15.5)'。"""
        cmds = _capture_ffmpeg(monkeypatch)
        ok = HyperframesRenderer.render_overlay_on_video(
            "ov.mov", "main.mp4", "out.mp4", start_sec=12.5, duration_sec=3.0)
        assert ok is True
        flt = _filter_complex(cmds[0])
        assert "overlay=format=auto:enable='between(t,12.5,15.5)'" in flt, flt

    def test_overlay_window_zero_start(self, monkeypatch) -> None:
        """起点为 0 的窗口同样生成 enable（0 是合法起点，不能当 None 处理）。"""
        cmds = _capture_ffmpeg(monkeypatch)
        HyperframesRenderer.render_overlay_on_video(
            "ov.mov", "main.mp4", "out.mp4", start_sec=0.0, duration_sec=5.0)
        assert "enable='between(t,0.0,5.0)'" in _filter_complex(cmds[0])

    def test_overlay_encoder_resolved(self, monkeypatch) -> None:
        """合成命令的 -c:v 使用探测编码器（与 Bug3 联动，不硬编码）。"""
        cmds = _capture_ffmpeg(monkeypatch)
        HyperframesRenderer.render_overlay_on_video(
            "ov.mov", "main.mp4", "out.mp4", start_sec=1.0, duration_sec=2.0)
        cmd = cmds[0]
        assert cmd[cmd.index("-c:v") + 1] == "libx264"


class TestApplyMGOverlayPassthrough:
    """RenderService._apply_mg_overlay 必须把 clip 的 start/duration 传入 overlay。"""

    async def test_window_passed_to_renderer(self, monkeypatch, tmp_path) -> None:
        recorded: dict = {}

        def fake_overlay(mov, video, out_v, start_sec=None, duration_sec=None):
            recorded.update(mov=mov, video=video, out=out_v,
                            start=start_sec, dur=duration_sec)
            return False  # 不产出文件，让 svc 回退返回原 video

        monkeypatch.setattr(
            HyperframesRenderer, "render_overlay_on_video",
            staticmethod(fake_overlay))
        svc = RenderService(work_dir=tmp_path)
        result = await svc._apply_mg_overlay(
            "main.mp4", "mg_out.mov", 1920, 1080, 30.0,
            start_sec=2.0, duration_sec=4.0)
        assert result == "main.mp4"  # 渲染失败回退原视频
        assert recorded["mov"] == "mg_out.mov"
        assert recorded["video"] == "main.mp4"
        assert recorded["start"] == 2.0
        assert recorded["dur"] == 4.0


class TestBuildHtmlDimsAttributes:
    """T1(C2a): _build_html 根 div 必须携带 data-width/data-height（=拟定分辨率，与内联样式一致）。"""

    def test_root_div_has_dim_attrs(self) -> None:
        html = HyperframesRenderer._build_html(
            [{"text": "hi", "start_sec": 0, "duration_sec": 2}], 1080, 1920, 30
        )
        # 根 [data-composition-id] div 必须携带 data-width/data-height（=拟定分辨率，与内联样式一致）
        assert re.search(
            r'<div id="root" data-composition-id="main" data-duration="2\.00"'
            r'\s+data-width="1080" data-height="1920"'
            r'\s+style="width:1080px;height:1920px;position:relative;overflow:hidden">',
            html,
        ) is not None

    def test_html_tag_fps_int(self) -> None:
        """<html> 标签 fps 为整数（不改变现有行为）。"""
        html = HyperframesRenderer._build_html(
            [{"text": "hi", "start_sec": 0, "duration_sec": 2}], 1920, 1080, 25
        )
        assert 'data-fps="25"' in html
