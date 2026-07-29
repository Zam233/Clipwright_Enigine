"""Schema 模型测试。"""

from __future__ import annotations

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


class TestTimeline:
    def test_empty_timeline(self) -> None:
        tl = Timeline()
        assert tl.total_duration_sec == 0.0

    def test_timeline_with_clips(self) -> None:
        track = Track(
            id="v1",
            name="Video",
            kind=ClipKind.VIDEO,
            index=0,
            clips=[
                Clip(
                    id="c1",
                    kind=ClipKind.VIDEO,
                    asset_id="a1",
                    track_id="v1",
                    start_sec=0,
                    duration_sec=10,
                ),
                Clip(
                    id="c2",
                    kind=ClipKind.VIDEO,
                    asset_id="a2",
                    track_id="v1",
                    start_sec=10,
                    duration_sec=20,
                ),
            ],
        )
        tl = Timeline(tracks=[track])
        assert tl.total_duration_sec == 30.0


class TestTrack:
    def test_track_creation(self) -> None:
        track = Track(id="a1", name="Audio", kind=ClipKind.AUDIO, index=0)
        assert track.clips == []
        assert not track.locked


class TestAnimationIntent:
    """AnimationIntent 模型测试。"""

    def test_minimal_creation(self) -> None:
        from clipwright.schema.agent import AnimationIntent
        i = AnimationIntent(description="产品对比动画", text_content="A|B")
        assert i.type == "mg"
        assert i.description == "产品对比动画"
        assert i.text_content == "A|B"
        assert i.scene_index is None

    def test_full_creation(self) -> None:
        from clipwright.schema.agent import AnimationIntent
        i = AnimationIntent(
            scene_index=2,
            type="logic",
            description="因果链条",
            text_content="原因|结果",
            style_hint="tech_dark",
            suggested_template="mg_comparison_split",
        )
        assert i.scene_index == 2
        assert i.type == "logic"
        assert i.style_hint == "tech_dark"

    def test_requirements_output_has_animation_intents(self) -> None:
        from clipwright.schema.agent import AnimationIntent, RequirementsOutput
        o = RequirementsOutput(
            creative_brief={"title": "test"},
            animation_intents=[
                AnimationIntent(description="desc", text_content="text"),
            ],
        )
        assert len(o.animation_intents) == 1
        assert o.animation_intents[0].description == "desc"

    def test_animation_output_has_generated_mg_count(self) -> None:
        from clipwright.schema.agent import AnimationOutput
        o = AnimationOutput(generated_mg_count=5)
        assert o.generated_mg_count == 5
