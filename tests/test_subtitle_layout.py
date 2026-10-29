"""字幕位置与换行宽度（用户 2026-10-28 反馈）。

三个现象里这两个是**字幕层**的：

1. 连续两句字幕短暂时重叠时，出现"一句在上面、一句在下面"；
2. 字幕条宽度不够，同一句话被折成多行。

根因：
1. `render._extract_text_overlay` 的兜底把 `track_idx` 当成了"层号"
   （`{1:"bottom", 2:"top", 3:"center"}`）——字幕落在第 2 条轨就变"顶部"。
   字幕位置必须**只认字幕轨的设置**。
2. `design.wrap_cjk_lines` 的每行上限写死 16 字：1920 宽 + 48px 字号实际能放
   ~36 字，写死 16 等于只用半幅画面 → 一句 18 字的字幕被折成两行。

第三个现象（成片没有动画）是 overlay 层：45 个图解/动画覆盖层整批渲染失败
（Hyperframes code=1），而旧实现静默跳过、渲染仍返回 success。
"""

from __future__ import annotations

from clipwright.services.render import RenderService
from clipwright.tool.design import chars_per_line, wrap_cjk_lines


class _Clip:
    def __init__(self, **kw):  # noqa: ANN003
        self.metadata = kw.get("metadata") or {}
        self.kind = kw.get("kind", "caption")
        self.text = kw.get("text", "")
        self.font_size = kw.get("font_size", 48)
        self.font_color = kw.get("font_color", "#ffffff")
        self.font = kw.get("font", "")
        self.start_sec = kw.get("start_sec", 0.0)
        self.duration_sec = kw.get("duration_sec", 1.0)
        self.keyframes = []


def test_caption_position_ignores_track_index():
    """字幕在第 2 条轨也不能被顶到画面顶部——位置以字幕轨设置为准。"""
    clip = _Clip(text="第一句", kind="caption")
    ov = RenderService._extract_text_overlay(clip, 2, [])

    assert ov["position"] == "bottom", f"字幕位置不该由 track_idx 决定，实际 {ov['position']}"


def test_explicit_caption_style_position_wins():
    clip = _Clip(text="第一句", kind="caption", metadata={"style": {"position": "top"}})
    ov = RenderService._extract_text_overlay(clip, 2, [])

    assert ov["position"] == "top", "显式配置优先"


def test_non_caption_text_still_uses_track_layers():
    """文字轨（非字幕）保留层号语义：第 2 层放顶部，避免两条文字叠一起。"""
    clip = _Clip(text="标题", kind="text", metadata={"category": "text"})
    ov = RenderService._extract_text_overlay(clip, 2, [])

    assert ov["position"] == "top"


def test_chars_per_line_uses_frame_width_and_font_size():
    assert chars_per_line(48) == 36, "1920×0.92/48 ≈ 36 字"
    assert chars_per_line(72) == 24
    assert chars_per_line(48, 1080) == 20
    assert chars_per_line(0) >= 16, "异常字号回落到可用值"


def test_short_caption_is_not_wrapped():
    text = "相关规定具体约束什么主体、什么行为？"  # 18 字
    assert wrap_cjk_lines(text, chars_per_line(48)) == text, "36 字上限下不该折行"


def test_long_caption_still_wraps_at_punctuation():
    text = "这是一句特别长的字幕" * 5
    out = wrap_cjk_lines(text, chars_per_line(48))
    assert "\n" in out.replace("\\N", "\n"), "超长句仍要折行"
    for line in out.replace("\\N", "\n").splitlines():
        assert len(line) <= chars_per_line(48) + 2
