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


class TestClipNewFields:
    """前端新增字段在后端 Clip 模型中的支持测试。"""

    def test_new_fields_defaults(self) -> None:
        clip = Clip(id="c1", kind=ClipKind.VIDEO, asset_id="a1", track_id="v1",
                    start_sec=0, duration_sec=5)
        assert clip.enabled is True
        assert clip.blend_mode is None
        assert clip.label_color is None
        assert clip.notes is None
        assert clip.eq_preset is None
        assert clip.fx_brightness is None
        assert clip.fx_contrast is None
        assert clip.fx_saturation is None
        assert clip.fx_blur is None
        assert clip.fx_hue is None

    def test_new_fields_set_and_serialize(self) -> None:
        clip = Clip(id="c1", kind=ClipKind.VIDEO, asset_id="a1", track_id="v1",
                    start_sec=0, duration_sec=5,
                    blend_mode="multiply", enabled=False, label_color="#FF0000",
                    notes="测试备注", eq_preset="bass-boost",
                    fx_brightness=1.2, fx_contrast=0.8, fx_saturation=1.5,
                    fx_blur=2.0, fx_hue=90.0)
        data = clip.model_dump(mode="json")
        assert data["blend_mode"] == "multiply"
        assert data["enabled"] is False
        assert data["label_color"] == "#FF0000"
        assert data["notes"] == "测试备注"
        assert data["eq_preset"] == "bass-boost"
        assert data["fx_brightness"] == 1.2
        assert data["fx_contrast"] == 0.8
        assert data["fx_saturation"] == 1.5
        assert data["fx_blur"] == 2.0
        assert data["fx_hue"] == 90.0

    def test_round_trip_preserves_fields(self) -> None:
        """模拟 pipeline 合并路径：Clip → dict → Clip，字段不丢失。"""
        clip = Clip(id="c1", kind=ClipKind.VIDEO, asset_id="a1", track_id="v1",
                    start_sec=0, duration_sec=5,
                    blend_mode="screen", enabled=False, fx_brightness=1.5)
        data = clip.model_dump(mode="json")
        restored = Clip(**data)
        assert restored.blend_mode == "screen"
        assert restored.enabled is False
        assert restored.fx_brightness == 1.5

    def test_extra_fields_preserved(self) -> None:
        """extra='allow' 确保前端自定义字段不被丢弃。"""
        clip = Clip(id="c1", kind=ClipKind.VIDEO, asset_id="a1", track_id="v1",
                    start_sec=0, duration_sec=5,
                    **{"custom_field": "hello"})  # type: ignore[call-arg]
        data = clip.model_dump(mode="json")
        assert data.get("custom_field") == "hello"
        restored = Clip(**data)
        assert getattr(restored, "custom_field", None) == "hello"

    def test_timeline_round_trip_with_new_fields(self) -> None:
        """完整 Timeline 序列化/反序列化保持新字段。"""
        track = Track(id="v1", name="Video", kind=ClipKind.VIDEO, index=0, clips=[
            Clip(id="c1", kind=ClipKind.VIDEO, asset_id="a1", track_id="v1",
                 start_sec=0, duration_sec=5, fx_blur=3.0, label_color="#00FF00"),
        ])
        tl = Timeline(tracks=[track])
        data = tl.model_dump(mode="json")
        restored = Timeline(**data)
        c = restored.tracks[0].clips[0]
        assert c.fx_blur == 3.0
        assert c.label_color == "#00FF00"
