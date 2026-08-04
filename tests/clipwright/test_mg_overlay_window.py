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
from pathlib import Path

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


class TestApplyMGChainedOverlay:
    """T3(C1): 单次 filter_complex 链式 MG overlay 合成（对比旧版 N 次全片 re-encode）。

    mock 策略：``_render_mg_mov`` 返回预先建好的假 .mov 文件路径（按 ``_track_idx``
    对应，顺序无关）；``_ff`` 记录命令行并返回 rc=0，不真正执行 ffmpeg。
    """

    def _mk(self, tmp_path, i: int) -> str:
        p = tmp_path / f"fake_mg_{i}.mov"
        p.write_bytes(b"\x00" * 4096)
        return str(p)

    def _hf(self, n: int):
        return [{"mg_html": "<html></html>", "start_sec": float(i), "duration_sec": 1.0,
                 "_track_idx": i} for i in range(n)]

    def _build(self, monkeypatch, tmp_path, returns: list[str | None]):
        """returns[i] = _track_idx 为 i 的 MG 返回的 MOV 路径（None = 渲染失败跳过）。"""
        monkeypatch.setattr(render_mod, "_resolve_encoder", lambda: "libx264")
        svc = RenderService(work_dir=tmp_path / "work")
        ff_cmds: list[list[str]] = []
        by_idx = dict(enumerate(returns))

        async def fake_ff(self, cmd, **kw):
            ff_cmds.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(RenderService, "_ff", fake_ff)

        async def fake_render_mg_mov(self, mg_ov, width, height, fps):
            return by_idx.get(mg_ov.get("_track_idx"), None)

        monkeypatch.setattr(RenderService, "_render_mg_mov", fake_render_mg_mov)
        return svc, ff_cmds

    async def test_chained_single_invocation(self, monkeypatch, tmp_path) -> None:
        """≥4 个 MG → 恰好 1 次 ffmpeg 调用；-i 数 = N+1；N 个 enable 窗口。"""
        n = 4
        svc, cmds = self._build(monkeypatch, tmp_path, [self._mk(tmp_path, i) for i in range(n)])
        await svc._apply_all_hyperframes("main.mp4", [], self._hf(n), 1920, 1080, 30.0)
        assert len(cmds) == 1, f"应只有 1 次 ffmpeg 调用: {len(cmds)}"
        cmd = cmds[0]
        assert sum(1 for t in cmd if t == "-i") == n + 1  # 主视频 + N 个 MOV
        flt = _filter_complex(cmd)
        assert flt.count("enable='between(t,") == n
        assert cmd[cmd.index("-map") + 1] == f"[v{n}]"

    async def test_chain_survives_missing_mov(self, monkeypatch, tmp_path) -> None:
        """一个 MOV=None → 跳过该输入，不断链、不崩溃，其余照常合成。"""
        returns: list[str | None] = [self._mk(tmp_path, 0), None, self._mk(tmp_path, 2)]
        svc, cmds = self._build(monkeypatch, tmp_path, returns)
        await svc._apply_all_hyperframes("main.mp4", [], self._hf(3), 1920, 1080, 30.0)
        assert len(cmds) == 1
        cmd = cmds[0]
        assert sum(1 for t in cmd if t == "-i") == 3  # 主视频 + 2 个有效 MOV
        flt = _filter_complex(cmd)
        assert flt.count("enable='between(t,") == 2
        assert cmd[cmd.index("-map") + 1] == "[v2]"

    async def test_cmdline_length_under_30000(self, monkeypatch, tmp_path) -> None:
        """20 个 MOV 的命令行总长 < 30000（Windows 命令行长度安全）。"""
        n = 20
        svc, cmds = self._build(monkeypatch, tmp_path, [self._mk(tmp_path, i) for i in range(n)])
        await svc._apply_all_hyperframes("main.mp4", [], self._hf(n), 1920, 1080, 30.0)
        assert cmds, "应至少产生一次 ffmpeg 调用"
        for cmd in cmds:
            assert len(" ".join(cmd)) < 30000

    async def test_progress_monotonic(self, monkeypatch, tmp_path) -> None:
        """整个 _apply_all_hyperframes 的 pct 序列单调不减（70 → 90 → 95 → 96）。"""
        n = 4
        svc, cmds = self._build(monkeypatch, tmp_path, [self._mk(tmp_path, i) for i in range(n)])
        text_overlays = [{"renderer": "hyperframes", "text": "x",
                          "start_sec": 0, "duration_sec": 1}]
        calls: list[tuple[str, float, str]] = []

        async def spy(phase, pct, detail):
            calls.append((phase, pct, detail))

        async def fake_render_overlays(overlays, output_path, width, height, fps):
            Path(output_path).write_bytes(b"\x00" * 4096)
            return True

        monkeypatch.setattr(HyperframesRenderer, "render_overlays", fake_render_overlays)
        monkeypatch.setattr(
            HyperframesRenderer, "render_overlay_on_video",
            staticmethod(lambda mov, video, out_v: True))
        await svc._apply_all_hyperframes("main.mp4", text_overlays, self._hf(n),
                                         1920, 1080, 30.0, spy)
        pcts = [c[1] for c in calls]
        assert pcts, "未收到任何进度回调"
        for a, b in zip(pcts, pcts[1:]):
            assert b >= a, f"进度回退: {a} -> {b} | 序列={pcts}"

    async def test_each_input_scaled_padded(self, monkeypatch, tmp_path) -> None:
        """每个 MOV 输入都带 scale/pad 尺寸对齐（导出分辨率，各出现一次）。"""
        n = 3
        svc, cmds = self._build(monkeypatch, tmp_path, [self._mk(tmp_path, i) for i in range(n)])
        await svc._apply_all_hyperframes("main.mp4", [], self._hf(n), 1920, 1080, 30.0)
        flt = _filter_complex(cmds[0])
        assert flt.count("scale=1920:1080:force_original_aspect_ratio=decrease") == n
        assert flt.count("pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1") == n
