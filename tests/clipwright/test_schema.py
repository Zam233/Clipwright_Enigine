"""Schema 模型测试。"""

from __future__ import annotations

import pytest

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

    def test_timeline_markers_round_trip(self) -> None:
        """M8: markers 字段序列化/反序列化保持，旧数据（无字段）解析为 []。"""
        tl = Timeline(markers=[{"time": 1.5, "name": "片头"}, {"time": 12.0}])
        data = tl.model_dump(mode="json")
        restored = Timeline(**data)
        assert restored.markers[0].time == 1.5
        assert restored.markers[0].name == "片头"
        assert restored.markers[1].time == 12.0
        assert restored.markers[1].name == ""
        # 旧数据兼容：无 markers 字段 → 空列表
        legacy = Timeline.model_validate({"tracks": [], "duration_sec": 0})
        assert legacy.markers == []
        # 名称长度上限：超过 64 字符应被拒绝（前端 input maxLength=64 同步限制）
        with pytest.raises(Exception):
            Timeline(markers=[{"time": 0, "name": "x" * 100}])


class TestCaptionStyleFields:
    """字幕样式字段（任务 28）在后端 Clip 模型中的支持测试。"""

    CAPTION_ARGS: dict = {
        "id": "c1",
        "kind": ClipKind.CAPTION,
        "asset_id": "a1",
        "track_id": "v1",
        "start_sec": 0,
        "duration_sec": 5,
    }

    def test_caption_style_fields_default_none(self) -> None:
        """缺省字段时，10 个新字段均为 None（旧数据无字段也能解析）。"""
        clip = Clip(**self.CAPTION_ARGS)
        assert clip.font_weight is None
        assert clip.font_italic is None
        assert clip.letter_spacing is None
        assert clip.stroke_width is None
        assert clip.stroke_color is None
        assert clip.shadow_x is None
        assert clip.shadow_y is None
        assert clip.shadow_color is None
        assert clip.shadow_blur is None
        assert clip.glow_color is None
        assert clip.glow_width is None

    def test_caption_style_fields_set_and_round_trip(self) -> None:
        """带全部新字段构造，序列化/反序列化后值不变（round-trip）。"""
        clip = Clip(
            **self.CAPTION_ARGS,
            font_weight="bold",
            font_italic=True,
            letter_spacing=2.5,
            stroke_width=3.0,
            stroke_color="#FF0000",
            shadow_x=4.0,
            shadow_y=-4.0,
            shadow_color="#000000",
            shadow_blur=6.0,
            glow_color="#FFFF00",
            glow_width=8.0,
        )
        data = clip.model_dump(mode="json")
        assert data["font_weight"] == "bold"
        assert data["font_italic"] is True
        assert data["letter_spacing"] == 2.5
        assert data["stroke_width"] == 3.0
        assert data["stroke_color"] == "#FF0000"
        assert data["shadow_x"] == 4.0
        assert data["shadow_y"] == -4.0
        assert data["shadow_color"] == "#000000"
        assert data["shadow_blur"] == 6.0
        assert data["glow_color"] == "#FFFF00"
        assert data["glow_width"] == 8.0

        restored = Clip(**data)
        assert restored.font_weight == "bold"
        assert restored.font_italic is True
        assert restored.letter_spacing == 2.5
        assert restored.stroke_width == 3.0
        assert restored.stroke_color == "#FF0000"
        assert restored.shadow_x == 4.0
        assert restored.shadow_y == -4.0
        assert restored.shadow_color == "#000000"
        assert restored.shadow_blur == 6.0
        assert restored.glow_color == "#FFFF00"
        assert restored.glow_width == 8.0

    def test_caption_style_constraints(self) -> None:
        """ge=0 约束字段拒绝负值；hex 颜色字段拒绝非法格式。"""
        import pytest
        for field, value in [("stroke_width", -1.0), ("shadow_blur", -1.0),
                             ("glow_width", -1.0)]:
            with pytest.raises(ValueError):
                Clip(**self.CAPTION_ARGS, **{field: value})
        for field, value in [("stroke_color", "red"), ("shadow_color", "#GGGGGG"),
                             ("glow_color", 123)]:
            with pytest.raises(ValueError):
                Clip(**self.CAPTION_ARGS, **{field: value})

    def test_caption_style_fields_inside_timeline_round_trip(self) -> None:
        """完整 Timeline 序列化/反序列化保持字幕样式字段。"""
        track = Track(id="v1", name="Subs", kind=ClipKind.CAPTION, index=0, clips=[
            Clip(
                **self.CAPTION_ARGS,
                font_weight="bold",
                stroke_width=2.0,
                shadow_blur=5.0,
                glow_color="#00FFFF",
            ),
        ])
        tl = Timeline(tracks=[track])
        data = tl.model_dump(mode="json")
        restored = Timeline(**data)
        c = restored.tracks[0].clips[0]
        assert c.font_weight == "bold"
        assert c.stroke_width == 2.0
        assert c.shadow_blur == 5.0
        assert c.glow_color == "#00FFFF"

    def test_extra_allow_still_preserved_with_caption_fields(self) -> None:
        """extra='allow' 在新增字段后仍不丢弃自定义字段（回归防护）。"""
        clip = Clip(
            **self.CAPTION_ARGS,
            font_weight="normal",
            **{"custom_field": "hello"},  # type: ignore[call-arg]
        )
        data = clip.model_dump(mode="json")
        assert data.get("custom_field") == "hello"
        assert data["font_weight"] == "normal"
