"""llm_mg Validator 单元测试。"""

from __future__ import annotations

from plugins.llm_mg.validator import validate_mg_json, repair_mg_json


class TestValidateMGJson:
    """validate_mg_json 函数测试。"""

    def _minimal_valid(self) -> dict:
        return {
            "animation_id": "mg_test",
            "duration_sec": 3.0,
            "width": 1920,
            "height": 1080,
            "elements": [
                {
                    "type": "text",
                    "content": "Hello",
                    "keyframes": [
                        {"time": 0, "opacity": 0},
                        {"time": 1.0, "opacity": 1},
                    ],
                }
            ],
        }

    def test_valid_minimal(self) -> None:
        """最小合法 MG JSON 通过验证。"""
        ok, errors = validate_mg_json(self._minimal_valid())
        assert ok is True
        assert errors == []

    def test_not_dict(self) -> None:
        """非 dict 输入返回失败。"""
        ok, errors = validate_mg_json("not a dict")  # type: ignore[arg-type]
        assert ok is False
        assert "not a dict" in errors[0]

    def test_missing_animation_id(self) -> None:
        """缺少 animation_id。"""
        d = self._minimal_valid()
        del d["animation_id"]
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("animation_id" in e for e in errors)

    def test_empty_animation_id(self) -> None:
        """animation_id 为空字符串。"""
        d = self._minimal_valid()
        d["animation_id"] = ""
        ok, errors = validate_mg_json(d)
        assert ok is False

    def test_missing_elements(self) -> None:
        """缺少 elements。"""
        d = self._minimal_valid()
        del d["elements"]
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("elements" in e for e in errors)

    def test_empty_elements(self) -> None:
        """elements 为空列表。"""
        d = self._minimal_valid()
        d["elements"] = []
        ok, errors = validate_mg_json(d)
        assert ok is False

    def test_invalid_duration(self) -> None:
        """duration_sec 为负数。"""
        d = self._minimal_valid()
        d["duration_sec"] = -1.0
        ok, _ = validate_mg_json(d)
        assert ok is False

    def test_invalid_dimensions(self) -> None:
        """width/height 无效。"""
        d = self._minimal_valid()
        d["width"] = 0
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("width" in e for e in errors)

    def test_invalid_element_type(self) -> None:
        """非法元素类型。"""
        d = self._minimal_valid()
        d["elements"][0]["type"] = "invalid_type"
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("type" in e for e in errors)

    def test_text_missing_content(self) -> None:
        """text 元素缺少 content。"""
        d = self._minimal_valid()
        del d["elements"][0]["content"]
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("content" in e for e in errors)

    def test_shape_missing_shape_field(self) -> None:
        """shape 元素缺少 shape 字段。"""
        d = self._minimal_valid()
        d["elements"][0]["type"] = "shape"
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("shape" in e for e in errors)

    def test_shape_invalid_value(self) -> None:
        """shape 值非法。"""
        d = self._minimal_valid()
        d["elements"][0] = {
            "type": "shape", "shape": "triangle",
            "keyframes": [
                {"time": 0, "opacity": 0},
                {"time": 1, "opacity": 1},
            ],
        }
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("shape" in e for e in errors)

    def test_too_few_keyframes(self) -> None:
        """keyframes 少于 2 个。"""
        d = self._minimal_valid()
        d["elements"][0]["keyframes"] = [{"time": 0, "opacity": 1}]
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("keyframes" in e for e in errors)

    def test_keyframe_time_out_of_range(self) -> None:
        """关键帧时间超出范围。"""
        d = self._minimal_valid()
        d["elements"][0]["keyframes"][1]["time"] = 999
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("time" in e for e in errors)

    def test_unknown_animatable_property(self) -> None:
        """未知动画属性。"""
        d = self._minimal_valid()
        d["elements"][0]["keyframes"][0]["foo"] = 42
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("foo" in e for e in errors)

    def test_shape_with_valid_properties(self) -> None:
        """合法 shape 元素通过验证。"""
        d = self._minimal_valid()
        d["elements"][0] = {
            "type": "shape", "shape": "rect", "color": "#ff0000",
            "keyframes": [
                {"time": 0, "opacity": 0, "width": 0},
                {"time": 1.5, "opacity": 1, "width": 200},
            ],
        }
        ok, errors = validate_mg_json(d)
        assert ok is True

    def test_multiple_elements(self) -> None:
        """多个元素全部合法。"""
        d = self._minimal_valid()
        d["elements"].append({
            "type": "text", "content": "World",
            "keyframes": [
                {"time": 0, "opacity": 0, "scale": 0.5},
                {"time": 1, "opacity": 1, "scale": 1.0},
            ],
        })
        ok, errors = validate_mg_json(d)
        assert ok is True


class TestRepairMGJson:
    """repair_mg_json 函数测试。"""

    def test_repair_missing_animation_id(self) -> None:
        """自动补充 animation_id。"""
        repaired, fixes = repair_mg_json({"elements": []})
        assert repaired["animation_id"].startswith("mg_generated_")
        assert any("animation_id" in f for f in fixes)

    def test_repair_missing_duration(self) -> None:
        """自动补充 duration_sec。"""
        repaired, fixes = repair_mg_json({"animation_id": "x", "elements": []})
        assert repaired["duration_sec"] == 3.0

    def test_repair_missing_dimensions(self) -> None:
        """自动补充 width/height。"""
        repaired, _ = repair_mg_json({"animation_id": "x", "elements": []})
        assert repaired["width"] == 1920
        assert repaired["height"] == 1080

    def test_repair_empty_keyframes(self) -> None:
        """空 keyframes 自动补充默认值。"""
        repaired, fixes = repair_mg_json({
            "animation_id": "x",
            "duration_sec": 2.0,
            "elements": [{"type": "text", "content": "Hi", "keyframes": []}],
        })
        kfs = repaired["elements"][0]["keyframes"]
        assert len(kfs) >= 2
        assert any("default keyframes" in f for f in fixes)

    def test_repair_no_time_zero_keyframe(self) -> None:
        """缺少 time=0 关键帧时自动补。"""
        repaired, fixes = repair_mg_json({
            "animation_id": "x",
            "elements": [{
                "type": "text", "content": "Hi",
                "keyframes": [{"time": 0.5, "opacity": 1}],
            }],
        })
        kfs = repaired["elements"][0]["keyframes"]
        assert kfs[0]["time"] == 0

    def test_repair_missing_style(self) -> None:
        """自动补充 style 字段。"""
        repaired, _ = repair_mg_json({
            "animation_id": "x",
            "elements": [{
                "type": "text", "content": "Hi",
                "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}],
            }],
        })
        assert "style" in repaired
        assert repaired["style"]["background"] == "transparent"

    def test_repair_adds_params_from_placeholders(self) -> None:
        """从 {text} {accent} 占位符自动生成 params。"""
        repaired, _ = repair_mg_json({
            "animation_id": "x",
            "elements": [{
                "type": "text", "content": "{text}",
                "font_color": "{accent}",
                "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}],
            }],
        })
        assert "params" in repaired
        assert "text" in repaired["params"]
        assert "accent" in repaired["params"]

    def test_repair_flattens_nested_properties(self) -> None:
        """将嵌套 properties 扁平化到 keyframe 顶层。"""
        repaired, _ = repair_mg_json({
            "animation_id": "x",
            "elements": [{
                "type": "text", "content": "Hi",
                "keyframes": [
                    {"time": 0, "properties": {"opacity": 0, "scale": 0.5}},
                    {"time": 1, "properties": {"opacity": 1, "scale": 1.0}},
                ],
            }],
        })
        kf = repaired["elements"][0]["keyframes"][0]
        assert kf["opacity"] == 0
        assert kf["scale"] == 0.5
