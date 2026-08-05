"""Bug4 回归测试 — 渲染进度全阶段上报。

基线（Baseline）：钉住当前各阶段 progress_callback 调用点存在。
Failing-first：对小型合成时间线真实跑一遍 ``RenderService.render``，
断言 progress 回调序列：
  1. 覆盖 ≥5 个不同 phase（prepare/trim/concat/text/mg/done）
  2. 百分比单调不减（prepare 不得在 trim 之后造成 5→0 回退）
  3. 最终到达 100

实现说明：仅 mock 两个外部重依赖（Hyperframes 可用性探测、MG HTML→MOV 渲染，
用一个真实 ffmpeg 生成的 0.5s 小 MOV 代替）；trim/concat/text/overlay 全部走
真实 ffmpeg 调用与真实回调接线——断言针对真实调用序列，非 mock 镜像。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import clipwright.services.render as render_mod
from clipwright.schema.timeline import Clip, Timeline, Track
from clipwright.services.render import RenderService


def _tiny_mov(path: Path) -> str:
    """用真实 ffmpeg 生成 0.5s 测试 MOV/MP4（替代 npx hyperframes 渲染）。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=red:s=320x240:d=0.5", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=60)
    return str(path)


def _timeline() -> Timeline:
    """1 个无源视频 clip（走 fallback 色块）+ 1 条字幕 + 1 个 MG 动画 clip。"""
    video = Clip(id="v1", kind="video", asset_id="", track_id="t0",
                 start_sec=0, duration_sec=1.0)
    caption = Clip(id="c1", kind="caption", asset_id="", track_id="t1",
                   start_sec=0, duration_sec=1.0, text="测试字幕")
    mg = Clip(id="m1", kind="animation", asset_id="", track_id="t2",
              start_sec=0, duration_sec=1.0,
              metadata={"renderer": "mg_hyperframes", "mg_html": "<html></html>"})
    return Timeline(
        id="tl_test", width=320, height=240, fps=10, duration_sec=1.0,
        tracks=[
            Track(id="t0", kind="video", index=0, clips=[video]),
            Track(id="t1", kind="caption", index=1, clips=[caption]),
            Track(id="t2", kind="animation", index=2, clips=[mg]),
        ],
    )


@pytest.fixture()
def _patched(monkeypatch, tmp_path):
    """只替换外部重依赖：HF 探测 + MG MOV 渲染；回调接线保持真实。"""
    monkeypatch.setattr(RenderService, "_hyperframes_available", staticmethod(lambda: True))
    monkeypatch.setattr(render_mod, "_RENDER_SEMAPHORE", None)
    mov = _tiny_mov(tmp_path / "fake_mg.mov")

    async def fake_render_mg_mov(self, mg_ov, width, height, fps):
        return mov

    monkeypatch.setattr(RenderService, "_render_mg_mov", fake_render_mg_mov)


class TestBaselinePinned:
    """基线特征化测试：钉住当前回调调用点（静态读取源码，防止静默删除）。"""

    def test_progress_callsites_exist(self) -> None:
        src = Path(render_mod.__file__).read_text(encoding="utf-8")
        for phase in ('"prepare"', '"trim"', '"concat"', '"text"', '"mg"', '"done"'):
            assert f"progress_callback({phase}" in src, f"缺少 {phase} 阶段回调"


class TestFullStageProgress:
    """Failing-first：全阶段进度序列。"""

    async def test_progress_full_pipeline(self, _patched, tmp_path) -> None:
        calls: list[tuple[str, float, str]] = []

        async def spy(phase, pct, detail):
            calls.append((phase, pct, detail))

        svc = RenderService(work_dir=tmp_path / "work")
        result = await svc.render(
            _timeline(), tmp_path / "out.mp4",
            width=320, height=240, fps=10, progress_callback=spy)

        assert result.success, f"渲染失败: {result.error} | log={result.ffmpeg_log}"
        assert calls, "未收到任何进度回调"

        phases = [c[0] for c in calls]
        distinct = set(phases)
        # ≥5 个不同阶段：prepare / trim / concat / text / mg / done
        assert len(distinct) >= 5, f"阶段不足: {distinct}"
        assert {"prepare", "trim", "concat", "mg", "done"} <= distinct

        # 百分比单调不减（prepare 5 → trim 0 的回退会在此失败）
        pcts = [c[1] for c in calls]
        for a, b in zip(pcts, pcts[1:]):
            assert b >= a, f"进度回退: {a} -> {b} | 序列={pcts}"

        # 最终到达 100
        assert pcts[-1] == 100, f"未到达 100: {pcts}"
