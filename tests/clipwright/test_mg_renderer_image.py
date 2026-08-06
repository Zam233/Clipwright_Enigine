# -*- coding: utf-8 -*-
"""MG 渲染器 image 元素支持测试 — ux-polish 计划 todo 26（A6）。

覆盖（真实渲染字符串断言，非 mock）：
1. _render_element 将 {"type":"image","src",...} 渲染为真实 <img> 标签，
   带正确 src / x / y / width / height（对抗 misleading_success_output）。
2. validator（_validate）schema 接受 image 类型，且拒绝畸形 image 元素
   （缺少 src / 空 src / 非数值 x / 缺失或非数值 width 等，对抗 malformed_input）。
3. image 元素支持 opacity/scale/translate 关键帧动画（与其他元素共用 keyframe 机制）。
4. LLM prompt（mg/config.yaml system_template）可用元素列表包含 image。
"""

from __future__ import annotations

from clipwright.animation.mg.generator import MGGenerator
from clipwright.animation.mg.validator import VALID_ELEMENT_TYPES, validate_mg_json
from clipwright.animation.mg_renderer import MGRenderer


def _image_def() -> dict:
    """合法的含 image 元素 MG 定义。"""
    return {
        "animation_id": "mg_generated_image_test",
        "name": "图片测试",
        "description": "image 元素渲染",
        "duration_sec": 4.0,
        "width": 1920,
        "height": 1080,
        "style": {"background": "transparent", "font_family": "sans-serif"},
        "params": {},
        "elements": [
            {
                "type": "image",
                "src": "assets/graphene.png",
                "x": 200,
                "y": 150,
                "width": 320,
                "height": 240,
                "keyframes": [
                    {"time": 0, "opacity": 0, "scale": 0.5, "translate_x": -20},
                    {"time": 1.0, "opacity": 1, "scale": 1.0, "translate_x": 0},
                ],
            }
        ],
    }


class TestImageElementRendering:
    """image 元素渲染为真实 <img>（真实渲染 HTML 字符串断言）。"""

    def test_renders_img_with_src_and_coords(self) -> None:
        html = MGRenderer.render(_image_def())
        assert "<img" in html
        assert 'src="assets/graphene.png"' in html
        assert 'id="mg-e0"' in html
        assert 'class="mg-el mg-image"' in html
        assert "position:absolute" in html
        assert "left:200px" in html
        assert "top:150px" in html
        assert "width:320px" in html
        assert "height:240px" in html

    def test_img_keyframes_opacity_scale_translate(self) -> None:
        """image 元素与 text/shape 共用关键帧机制：opacity/scale/translate 进 @keyframes。"""
        html = MGRenderer.render(_image_def())
        assert "@keyframes mg_anim_0{" in html
        assert "scale(0.5) translateX(-20px)" in html
        assert "scale(1.0) translateX(0px)" in html
        assert "opacity:0" in html
        assert "opacity:1" in html
        assert "el.style.animation='mg_anim_0 4.0s linear forwards';" in html

    def test_img_src_placeholder_filled(self) -> None:
        """src 支持 {placeholder} 参数替换（与 content 等字段一致）。"""
        d = _image_def()
        d["elements"][0]["src"] = "{img}"
        html = MGRenderer.render(d, {"img": "assets/logo.png"})
        assert 'src="assets/logo.png"' in html

    def test_img_without_keyframes_skipped(self) -> None:
        """无 keyframes 的 image 元素与其他元素一致：跳过不渲染。"""
        d = _image_def()
        d["elements"][0].pop("keyframes")
        html = MGRenderer.render(d)
        assert "mg-e0" not in html


class TestImageElementValidation:
    """validator schema：接受 image，拒绝畸形元素。"""

    def test_image_type_in_whitelist(self) -> None:
        assert "image" in VALID_ELEMENT_TYPES

    def test_valid_image_element_accepted(self) -> None:
        ok, errors = validate_mg_json(_image_def())
        assert ok is True, errors

    def test_mixed_text_and_image_accepted(self) -> None:
        d = _image_def()
        d["elements"].insert(0, {
            "type": "text", "content": "标题",
            "x": "center", "y": "center",
            "keyframes": [
                {"time": 0, "opacity": 0},
                {"time": 1.0, "opacity": 1},
            ],
        })
        ok, errors = validate_mg_json(d)
        assert ok is True, errors

    def test_image_missing_src_rejected(self) -> None:
        d = _image_def()
        del d["elements"][0]["src"]
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("src" in e for e in errors)

    def test_image_empty_src_rejected(self) -> None:
        d = _image_def()
        d["elements"][0]["src"] = "   "
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("src" in e for e in errors)

    def test_image_non_numeric_x_rejected(self) -> None:
        d = _image_def()
        d["elements"][0]["x"] = "abc"
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("x" in e for e in errors)

    def test_image_missing_width_rejected(self) -> None:
        d = _image_def()
        del d["elements"][0]["width"]
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("width" in e for e in errors)

    def test_image_non_numeric_width_rejected(self) -> None:
        d = _image_def()
        d["elements"][0]["width"] = "wide"
        ok, errors = validate_mg_json(d)
        assert ok is False
        assert any("width" in e for e in errors)


class TestLLMPromptElementList:
    """LLM prompt（config.yaml system_template）可用元素列表必须包含 image。"""

    def test_prompt_element_list_mentions_image(self) -> None:
        cfg = MGGenerator._load_config()
        template = cfg["prompt"]["system_template"]
        assert "可用元素种类" in template
        assert "- image:" in template