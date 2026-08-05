"""Q1+Q4 (T1) 字幕时长精确衔接 + 样式注入测试。

覆盖计划 acceptance criteria：
- 相邻 clip 无重叠（next.start >= prev.start + prev.dur - 0.01，全轨含跨场景）
- 每场景 Σdur == total_dur ±0.01
- 退化用例 10 短句 × 1.5s：合并后无重叠且 Σ <= 1.5 + 0.01
- 字幕 Clip 显式样式属性（stroke_width==2.0 / shadow_x==1.0 等，非默认全 0）
- overlay 提取 + TextStyle.from_dict → ASS 含 bord2 / shad1 标签
"""

from __future__ import annotations

from clipwright.agents.edit_agent import _append_caption_sentences
from clipwright.schema.timeline import ClipKind, Track
from clipwright.services.render import RenderService
from clipwright.tool.design import TextStyle


def _make_track() -> Track:
    return Track(id="t_caption", name="字幕轨", kind=ClipKind.CAPTION, index=2)


def _scenes_sum(clips: list, start_sec: float, end_sec: float) -> float:
    return sum(
        c.duration_sec for c in clips
        if start_sec - 0.001 <= c.start_sec < end_sec - 0.001
    )


class TestCaptionNoOverlap:
    def test_two_scenes_adjacent_no_overlap(self) -> None:
        """2 场景骨架：相邻 clip 无重叠 + 每场景 Σ==total_dur。"""
        track = _make_track()
        _append_caption_sentences(
            track, ["第一句话。", "第二句话比较长。", "第三句。"], 0.0, 8.0,
        )
        _append_caption_sentences(
            track, ["下一场景首句。", "下一场景次句。"], 8.0, 6.0,
        )
        clips = sorted(track.clips, key=lambda c: c.start_sec)
        # ① 相邻无重叠（含跨场景边界）
        for prev, nxt in zip(clips, clips[1:]):
            assert nxt.start_sec >= prev.start_sec + prev.duration_sec - 0.01, (
                f"overlap: {prev.start_sec}+{prev.duration_sec} -> {nxt.start_sec}"
            )
        # ② 每场景 Σ==total_dur
        assert abs(_scenes_sum(clips, 0.0, 8.0) - 8.0) <= 0.01
        assert abs(_scenes_sum(clips, 8.0, 14.0) - 6.0) <= 0.01

    def test_sum_exact_scene_one(self) -> None:
        """单场景多句：Σdur == total_dur ±0.01。"""
        track = _make_track()
        _append_caption_sentences(
            track, ["甲", "乙", "丙", "丁", "戊"], 1.0, 10.0,
        )
        total = sum(c.duration_sec for c in track.clips)
        assert abs(total - 10.0) <= 0.01
        assert abs(track.clips[-1].start_sec + track.clips[-1].duration_sec - 11.0) <= 0.01

    def test_degenerate_10_short_sentences_1_5s(self) -> None:
        """退化用例：10 短句 × 1.5s 场景 → 尾部合并，无重叠且 Σ<=1.5+0.01。"""
        track = _make_track()
        sentences = [f"短{i}" for i in range(10)]
        _append_caption_sentences(track, sentences, 0.0, 1.5)
        clips = sorted(track.clips, key=lambda c: c.start_sec)
        for prev, nxt in zip(clips, clips[1:]):
            assert nxt.start_sec >= prev.start_sec + prev.duration_sec - 0.01, (
                f"overlap: {prev.start_sec}+{prev.duration_sec} -> {nxt.start_sec}"
            )
        total = sum(c.duration_sec for c in clips)
        assert total <= 1.5 + 0.01, f"Σdur {total:.3f} > 1.5"
        # 末句不越界到下一场景
        assert clips[-1].start_sec + clips[-1].duration_sec <= 1.5 + 0.01
        # 合并确实发生：句数减少
        assert len(clips) < len(sentences)


class TestCaptionStyleInjected:
    def test_style_kwargs_non_default(self) -> None:
        """字幕 Clip 显式样式属性：stroke_width/shadow 非默认全 0。"""
        track = _make_track()
        _append_caption_sentences(track, ["一段字幕文字。"], 0.0, 4.0)
        clip = track.clips[0]
        assert clip.stroke_width == 2.0
        assert clip.stroke_color == "#000000"
        assert clip.shadow_x == 1.0
        assert clip.shadow_y == 1.0
        assert clip.shadow_color == "#80000000"

    def test_overlay_to_ass_has_bord_shad(self) -> None:
        """overlay 提取 + TextStyle.from_dict → ASS 含 \\bord2 / \\shad1 标签。"""
        track = _make_track()
        _append_caption_sentences(track, ["样式注入字幕。"], 0.0, 4.0)
        clip = track.clips[0]
        # 复现 render.py _extract_text_overlay 的样式提取路径
        svc = RenderService(work_dir=".")
        ov = svc._extract_text_overlay(clip, 2, [])
        ts = TextStyle.from_dict(ov["style"])
        d = ts.build_ass_dialogue(clip.text or "", clip.start_sec,
                                  clip.start_sec + clip.duration_sec)
        assert r"\bord2" in d
        assert r"\shad1" in d
