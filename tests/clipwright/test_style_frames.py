from clipwright.animation.brief import build_brief
from clipwright.animation.style_frames import build_style_frames


def test_three_style_frames_have_prompt_and_palette() -> None:
    frames = build_style_frames(build_brief("数据增长", {"display_name": "知识区"}, 5))
    assert len(frames) == 3
    assert {f.style_id for f in frames} >= {"swiss-editorial", "data-story"}
    assert all(f.prompt and len(f.palette) >= 2 for f in frames)
