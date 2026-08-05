"""任务 32 — 后端字幕默认样式统一测试。

验证新生成的 caption/text clip 携带完整样式字段（与前端对齐）：
- subtitle.py segments_to_timeline_clips
- animation_agent.py _handle_caption / _handle_text_animation

默认值统一：font_size=48、font_color=#ffffff、font_weight=normal、
font_italic=false、letter_spacing=0、text_align=center（caption）/left（text）、
stroke_width=0、stroke_color=#000000、shadow 全关、glow 关。
"""

from __future__ import annotations

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.schema.timeline import Clip, ClipKind, Track
from clipwright.services.subtitle import SubtitleSegment, segments_to_timeline_clips


def _video_clip(**kw: object) -> Clip:
    base = dict(
        id="v1", kind=ClipKind.VIDEO, asset_id="a1", track_id="t1",
        start_sec=1.0, duration_sec=5.0,
    )
    base.update(kw)
    return Clip(**base)  # type: ignore[arg-type]


# 完整样式字段清单（与 schema/timeline.py 任务 28 对齐）
STYLE_FIELDS = (
    "font_size", "font_color", "font_weight", "font_italic", "letter_spacing",
    "text_align", "stroke_width", "stroke_color",
    "shadow_x", "shadow_y", "shadow_color", "shadow_blur",
    "glow_color", "glow_width",
)


def _assert_complete_style_fields(clip: dict | Clip, align: str = "center") -> None:
    """断言 clip 携带完整样式字段且为统一默认值。"""
    data = clip.model_dump() if isinstance(clip, Clip) else clip
    for f in STYLE_FIELDS:
        assert f in data, f"缺少样式字段: {f}"
    assert data["font_size"] == 48
    assert data["font_color"] == "#ffffff"
    assert data["font_weight"] == "normal"
    assert data["font_italic"] is False
    assert data["letter_spacing"] == 0
    assert data["text_align"] == align
    assert data["stroke_width"] == 0
    assert data["stroke_color"] == "#000000"
    # shadow 全关
    assert data["shadow_x"] is None
    assert data["shadow_y"] is None
    assert data["shadow_color"] is None
    assert data["shadow_blur"] is None
    # glow 关
    assert data["glow_color"] is None
    assert data["glow_width"] is None


class TestSegmentsToTimelineClips:
    """subtitle.py segments_to_timeline_clips 样式字段测试。"""

    def test_caption_clip_complete_style(self) -> None:
        segs = [SubtitleSegment(1, 1.0, 3.0, "你好世界")]
        clips = segments_to_timeline_clips(segs)
        assert len(clips) == 1
        c = clips[0]
        assert c["kind"] == "caption"
        _assert_complete_style_fields(c, align="center")

    def test_multiple_segments_all_styled(self) -> None:
        segs = [
            SubtitleSegment(1, 1.0, 3.0, "第一句"),
            SubtitleSegment(2, 4.0, 6.0, "第二句"),
        ]
        clips = segments_to_timeline_clips(segs)
        assert len(clips) == 2
        for c in clips:
            _assert_complete_style_fields(c, align="center")


class TestAnimationAgentCaptionStyle:
    """animation_agent _handle_caption 样式字段测试。"""

    def _track(self) -> Track:
        return Track(id="t2", name="文字轨", kind=ClipKind.TEXT, index=1)

    def test_handle_caption_complete_style(self) -> None:
        agent = AnimationAgent()
        track = self._track()
        vid = _video_clip()
        agent._handle_caption(track, vid, "这是一段非常非常长的字幕文字内容超过五十个字触发长文本路由", {})
        assert len(track.clips) == 1
        c = track.clips[0]
        assert c.kind == ClipKind.CAPTION
        _assert_complete_style_fields(c, align="center")

    def test_handle_caption_persona_style_override(self) -> None:
        """persona_style 显式提供的值优先于默认。"""
        agent = AnimationAgent()
        track = self._track()
        vid = _video_clip()
        agent._handle_caption(track, vid, "很长很长的字幕文字内容超过五十个字触发长文本路由处理逻辑", {
            "font_size": 60,
            "font_color": "#ff0000",
        })
        c = track.clips[0]
        assert c.font_size == 60
        assert c.font_color == "#ff0000"
        # 未覆盖字段仍为默认
        assert c.font_weight == "normal"
        assert c.stroke_width == 0
        assert c.shadow_x is None


class TestAnimationAgentTextStyle:
    """animation_agent _handle_text_animation 样式字段测试。"""

    def _track(self) -> Track:
        return Track(id="t2", name="文字轨", kind=ClipKind.TEXT, index=1)

    def test_text_clip_complete_style(self) -> None:
        agent = AnimationAgent()
        track = self._track()
        vid = _video_clip()
        agent._handle_text_animation(
            track, vid, "text_fade_in", "淡入", {"text": "AI 改变世界"}, {},
        )
        assert len(track.clips) == 1
        c = track.clips[0]
        assert c.kind == ClipKind.TEXT
        _assert_complete_style_fields(c, align="left")

    def test_text_clip_persona_style_override(self) -> None:
        agent = AnimationAgent()
        track = self._track()
        vid = _video_clip()
        agent._handle_text_animation(
            track, vid, "text_fade_in", "淡入", {"text": "标题内容"},
            {"font_size": 72, "font_italic": True, "text_align": "center"},
        )
        c = track.clips[0]
        assert c.font_size == 72
        assert c.font_italic is True
        assert c.text_align == "center"
        # 未覆盖字段仍为默认
        assert c.font_weight == "normal"
        assert c.stroke_width == 0
        assert c.shadow_color is None
        assert c.glow_width is None
