"""AnimationAgent 降级路径 — 禁止把动画设计描述打印为屏幕文字。

背景（Task 2 / 降级文字修复）：
当 Hyperframes 不可用或 MG 渲染失败时，原实现会创建 TEXT/drawtext clip，
把 ``f"{anim_name}: {text_content[:50]}"``（LLM 生成的动画设计描述）直接渲染成
视频字幕，造成「因果: 镜像→他者→…」这类污染。本文件保证降级路径不再产出任何
屏幕文字：跳过创建 clip，仅记录 warning 日志 + trace 警告事件。

(a) BASELINE  —— 文档化修复前的 bug 行为（修复前通过，修复后 xfail）。
(b) FAILING-FIRST —— 断言降级路径不再创建描述文字 clip（修复前失败，修复后通过）。
(c) REGRESSION —— Hyperframes 可用 → DiagramSVG 正常路径仍创建 ANIMATION clip。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.schema.timeline import Clip, ClipKind, Track


def _mk_anim_track() -> Track:
    return Track(id="t1", name="动画轨", kind=ClipKind.ANIMATION, index=2)


def _mk_video_clip(start_sec: float = 2.0, duration_sec: float = 5.0) -> Clip:
    return Clip(
        id="vid1", kind=ClipKind.VIDEO, asset_id="a.mp4",
        track_id="t0", start_sec=start_sec, duration_sec=duration_sec,
        metadata={"description": "[逻辑动画]因果：镜像→他者→镜像"},
    )


def _mk_causation_marker() -> dict:
    return {
        "type": "logic", "anim_id": "causation", "name": "因果",
        "text": "镜像→他者→镜像",
    }


def _force_hyperframes(monkeypatch, available: bool) -> None:
    """mock ``_hyperframes_available``（静态方法）返回固定可用性。"""
    async def _fake(timeout: float = 120.0) -> bool:
        return available

    monkeypatch.setattr(
        AnimationAgent, "_hyperframes_available", staticmethod(_fake),
    )


class TestLogicAnimationDegrade:
    """Hyperframes 不可用时逻辑动画的降级行为。"""

    @pytest.mark.xfail(
        reason="BASELINE 文档化修复前 bug：降级路径曾创建 '因果: …' 描述文字 clip；"
               "修复后该行为已消除（xfail 防止修复回退时静默 XPASS）",
        strict=True,
    )
    def test_baseline_degrade_renders_description_as_caption(self, monkeypatch) -> None:
        """(a) BASELINE：修复前行为——降级路径创建 TEXT clip，文字为 anim_name 前缀。

        该测试断言的是修复前的 bug：动画设计描述被渲染成屏幕字幕
        （例如 ``因果: 镜像→他者→镜像``）。修复后降级路径跳过创建 clip，
        此测试将期望失败（xfail）以保留文档价值。
        """
        _force_hyperframes(monkeypatch, available=False)
        agent = AnimationAgent()
        track = _mk_anim_track()
        vid = _mk_video_clip()

        asyncio.run(agent._handle_logic_animation(
            track, vid, "causation", "因果", _mk_causation_marker(), None))

        assert len(track.clips) == 1, (
            f"降级路径应创建 1 个 clip，实际 {len(track.clips)}"
        )
        assert track.clips[0].text.startswith("因果:"), (
            f"clip 文字应以动画名开头，实际: {track.clips[0].text!r}"
        )

    def test_degrade_skips_clip_no_description_text(self, monkeypatch) -> None:
        """(b) FAILING-FIRST：降级路径不创建任何新 clip（无描述文字上屏）。"""
        _force_hyperframes(monkeypatch, available=False)
        agent = AnimationAgent()
        track = _mk_anim_track()
        vid = _mk_video_clip()

        asyncio.run(agent._handle_logic_animation(
            track, vid, "causation", "因果", _mk_causation_marker(), None))

        assert len(track.clips) == 0, (
            f"降级路径不应创建 clip，实际 {len(track.clips)}: "
            f"{[c.text for c in track.clips]}"
        )
        assert all(not getattr(c, "text", "").startswith("因果:")
                   for c in track.clips)

    def test_degrade_logs_warning_and_trace_marker(self, monkeypatch, caplog) -> None:
        """降级路径仍记录 warning 日志 + trace 警告事件（不静默丢弃动画）。"""
        import logging

        import clipwright.services.trace as trace
        captured: list[tuple] = []
        monkeypatch.setattr(
            trace, "add_event",
            lambda *a, **kw: captured.append((a[1], a[2], a[3], a[4])),
        )

        _force_hyperframes(monkeypatch, available=False)
        agent = AnimationAgent()
        agent._pid = "proj_degrade_test"
        track = _mk_anim_track()
        vid = _mk_video_clip()

        with caplog.at_level(logging.WARNING, logger="clipwright"):
            asyncio.run(agent._handle_logic_animation(
                track, vid, "causation", "因果", _mk_causation_marker(), None))

        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "Hyperframes" in joined, f"缺少降级 warning 日志: {joined}"
        assert any(ev[1] == "warning" for ev in captured), (
            f"缺少 trace 警告事件: {captured}"
        )


class TestLogicAnimationHyperframesPath:
    """(c) REGRESSION：Hyperframes 可用 → DiagramSVG 正常路径保持 ANIMATION clip。"""

    def test_hyperframes_available_creates_animation_clip(self, monkeypatch) -> None:
        """diagram_params 必须存在且 renderer=hyperframes，而非 drawtext TEXT clip。"""
        _force_hyperframes(monkeypatch, available=True)
        agent = AnimationAgent()
        track = _mk_anim_track()
        vid = _mk_video_clip()

        asyncio.run(agent._handle_logic_animation(
            track, vid, "causation", "因果", _mk_causation_marker(), None))

        assert len(track.clips) == 1
        produced = track.clips[0]
        assert str(produced.kind) == "animation", (
            f"Hyperframes 可用时应为 ANIMATION clip，实际 kind={produced.kind}"
        )
        assert produced.metadata["renderer"] == "hyperframes"
        assert produced.metadata["diagram_params"]["preset"] == "causation"
