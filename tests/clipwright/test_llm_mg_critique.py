"""llm_mg Generator LLM 自批判质量闭环单元测试。

覆盖三路径：高分通过 / 低分修复成功 / 低分修复失败降级 fallback，
以及批判禁用、LLM 批判失败静默跳过、问题不可修复等边界场景。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import yaml

from clipwright.animation.mg.generator import MGGenerator

CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "clipwright" / "animation" / "mg" / "config.yaml"
)


def _valid_mg_def(animation_id: str = "mg_test", name: str = "测试动画") -> dict[str, Any]:
    """构造一个通过 schema 校验的 MG 定义（渲染器可正常输出 HTML）。"""
    return {
        "animation_id": animation_id,
        "name": name,
        "description": "测试动画",
        "duration_sec": 3.0,
        "width": 1920,
        "height": 1080,
        "elements": [
            {
                "type": "text",
                "content": "{text}",
                "x": "center",
                "y": "center",
                "font_size": 72,
                "font_weight": "bold",
                "font_color": "#ffffff",
                "text_shadow": "0 0 30px rgba(79,140,255,0.8)",
                "keyframes": [
                    {"time": 0, "opacity": 0, "scale": 0.6, "easing": "back-out"},
                    {"time": 0.5, "opacity": 1, "scale": 1.0},
                    {"time": 2.7, "opacity": 1, "easing": "ease-in"},
                    {"time": 3.0, "opacity": 0, "translate_y": -16},
                ],
            },
            {
                "type": "text",
                "content": "{subtitle}",
                "x": "center",
                "y": "center",
                "y_offset": 80,
                "font_size": 40,
                "font_color": "#9fb4ff",
                "letter_spacing": 4,
                "keyframes": [
                    {"time": 0, "opacity": 0, "translate_y": 12, "easing": "ease-out"},
                    {"time": 1.2, "opacity": 1, "translate_y": 0},
                    {"time": 2.8, "opacity": 1},
                ],
            },
        ],
        "params": {
            "text": {"type": "string", "default": "帧艺"},
            "subtitle": {"type": "string", "default": "AI 视频创作"},
        },
        "style": {"background": "transparent", "font_family": "sans-serif"},
    }


def _resp(content: str) -> SimpleNamespace:
    """构造带 .content 属性的假 LLM 响应。"""
    return SimpleNamespace(content=content)


def _make_generator(responses: list[Any]) -> tuple[MGGenerator, AsyncMock]:
    """构造 MGGenerator 并用假 LLM 替换内部 _llm。"""
    gen = MGGenerator()
    fake = AsyncMock()
    fake.generate.side_effect = responses
    gen._llm = fake
    return gen, fake


class TestCritiqueConfig:
    """config.yaml critique 段配置测试。"""

    def test_critique_section_present(self) -> None:
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        critique = cfg.get("critique")
        assert isinstance(critique, dict)
        assert critique.get("enabled") is True
        assert critique.get("min_score") == 60


class TestCritiquePassThrough:
    """批判通过/跳过路径。"""

    async def test_high_score_passes_through(self) -> None:
        """高分通过：不触发修复，返回原始 mg_def（method=llm）。"""
        mg_def = _valid_mg_def()
        gen, fake = _make_generator([
            _resp(json.dumps(mg_def, ensure_ascii=False)),
            _resp(json.dumps({"score": 85, "issues": [], "suggestions": []})),
        ])
        result = await gen.generate("展示产品核心数据", "")
        assert result["success"] is True
        assert result["method"] == "llm"
        assert result["mg_def"]["animation_id"] == "mg_test"
        # 生成 + 批判，共 2 次 LLM 调用，无修复
        assert fake.generate.await_count == 2

        # 批判提示词必须注入评审角色与设计原则
        critique_messages = fake.generate.await_args_list[1].kwargs["messages"]
        critique_prompt = "".join(m["content"] for m in critique_messages)
        assert "Motion Graphics" in critique_prompt
        assert "score" in critique_prompt
        assert "Easing" in critique_prompt

    async def test_low_score_but_not_enabled_skips_critique(self) -> None:
        """critique.enabled=false：跳过批判，直接接受输出（即使低分）。"""
        mg_def = _valid_mg_def()
        gen, fake = _make_generator([
            _resp(json.dumps(mg_def, ensure_ascii=False)),
            _resp(json.dumps({"score": 10, "issues": ["太差"], "suggestions": ["重做"]})),
        ])
        gen._config["critique"] = {"enabled": False, "min_score": 60}
        result = await gen.generate("展示产品核心数据", "")
        assert result["method"] == "llm"
        assert result["success"] is True
        # 只调用生成，未调用批判
        assert fake.generate.await_count == 1

    async def test_critique_llm_failure_silent_skip(self) -> None:
        """批判 LLM 调用抛异常：静默跳过批判，不降级，接受原输出。"""
        mg_def = _valid_mg_def()
        gen, fake = _make_generator([
            _resp(json.dumps(mg_def, ensure_ascii=False)),
            RuntimeError("critique boom"),
        ])
        result = await gen.generate("展示产品核心数据", "")
        assert result["method"] == "llm"
        assert result["success"] is True
        assert result["mg_def"]["animation_id"] == "mg_test"

    async def test_critique_unparseable_silent_skip(self) -> None:
        """批判响应无法解析：静默跳过批判，接受原输出。"""
        mg_def = _valid_mg_def()
        gen, fake = _make_generator([
            _resp(json.dumps(mg_def, ensure_ascii=False)),
            _resp("this is not a critique json"),
        ])
        result = await gen.generate("展示产品核心数据", "")
        assert result["method"] == "llm"
        assert result["success"] is True

    async def test_critique_score_out_of_range_silent_skip(self) -> None:
        """批判评分越界视为无效：静默跳过批判。"""
        mg_def = _valid_mg_def()
        gen, fake = _make_generator([
            _resp(json.dumps(mg_def, ensure_ascii=False)),
            _resp(json.dumps({"score": 150, "issues": [], "suggestions": []})),
        ])
        result = await gen.generate("展示产品核心数据", "")
        assert result["method"] == "llm"
        assert result["success"] is True


class TestCritiqueRepair:
    """低分修复路径。"""

    async def test_low_score_repaired(self) -> None:
        """低分且可修复：带批判反馈修复一次，返回修复结果（method=critique_repair）。"""
        mg_def = _valid_mg_def()
        improved = _valid_mg_def(animation_id="mg_improved", name="改进版")
        gen, fake = _make_generator([
            _resp(json.dumps(mg_def, ensure_ascii=False)),
            _resp(json.dumps({
                "score": 42,
                "issues": ["元素只有 2 个，未达 >=4", "缺少粒子层"],
                "suggestions": ["增加背景层与粒子元素"],
            })),
            _resp(json.dumps(improved, ensure_ascii=False)),
        ])
        result = await gen.generate("展示产品核心数据", "")
        assert result["success"] is True
        assert result["method"] == "critique_repair"
        assert result["mg_def"]["animation_id"] == "mg_improved"
        # 生成 + 批判 + 批判修复，恰好一次修复（有界）
        assert fake.generate.await_count == 3

        # 修复请求必须携带批判反馈（评分/问题/建议）
        repair_messages = fake.generate.await_args.kwargs["messages"]
        repair_prompt = "".join(m["content"] for m in repair_messages)
        assert "42" in repair_prompt
        assert "增加背景层与粒子元素" in repair_prompt
        assert "元素只有 2 个" in repair_prompt

    async def test_low_score_repair_failure_falls_back(self) -> None:
        """低分且修复失败：降级到 fallback（无模板可匹配 → success=False）。"""
        mg_def = _valid_mg_def()
        gen, fake = _make_generator([
            _resp(json.dumps(mg_def, ensure_ascii=False)),
            _resp(json.dumps({
                "score": 30,
                "issues": ["动画平淡"],
                "suggestions": ["加强动效"],
            })),
            _resp("invalid repair response"),
        ])
        # 与任务 5 的模板目录状态解耦：显式清空模板，使降级结果可判定
        gen._get_templates = lambda: []  # type: ignore[method-assign]
        result = await gen.generate("展示产品核心数据", "")
        assert result["method"] == "fallback"
        assert result["success"] is False
        assert fake.generate.await_count == 3

    async def test_low_score_repair_invalid_json_falls_back(self) -> None:
        """修复返回非法 JSON（未过校验）：降级到 fallback。"""
        mg_def = _valid_mg_def()
        gen, fake = _make_generator([
            _resp(json.dumps(mg_def, ensure_ascii=False)),
            _resp(json.dumps({"score": 25, "issues": ["缺少缓动"], "suggestions": ["加 easing"]})),
            _resp(json.dumps({"animation_id": "", "elements": []})),
        ])
        gen._get_templates = lambda: []  # type: ignore[method-assign]
        result = await gen.generate("展示产品核心数据", "")
        assert result["method"] == "fallback"
        assert result["success"] is False

    async def test_low_score_not_fixable_accepts_original(self) -> None:
        """低分但问题不可修复：不触发修复，接受原输出。"""
        mg_def = _valid_mg_def()
        gen, fake = _make_generator([
            _resp(json.dumps(mg_def, ensure_ascii=False)),
            _resp(json.dumps({
                "score": 20,
                "issues": ["需求信息不足，无法实现"],
                "suggestions": [],
            })),
        ])
        result = await gen.generate("展示产品核心数据", "")
        assert result["method"] == "llm"
        assert result["success"] is True
        assert fake.generate.await_count == 2


class TestParseCritique:
    """_parse_critique 归一化测试。"""

    def setup_method(self) -> None:
        self.gen = MGGenerator()

    def test_valid(self) -> None:
        c = self.gen._parse_critique(
            json.dumps({"score": 75, "issues": ["a"], "suggestions": ["b"]})
        )
        assert c == {"score": 75, "issues": ["a"], "suggestions": ["b"]}

    def test_score_as_string(self) -> None:
        c = self.gen._parse_critique(
            json.dumps({"score": "62", "issues": [], "suggestions": []})
        )
        assert c["score"] == 62

    def test_score_as_float(self) -> None:
        c = self.gen._parse_critique(
            json.dumps({"score": 62.7, "issues": [], "suggestions": []})
        )
        assert c["score"] == 62

    def test_missing_issues_suggestions(self) -> None:
        c = self.gen._parse_critique(json.dumps({"score": 50}))
        assert c["issues"] == []
        assert c["suggestions"] == []

    def test_suggestions_as_single_string(self) -> None:
        c = self.gen._parse_critique(json.dumps({"score": 50, "suggestions": "改进动效"}))
        assert c["suggestions"] == ["改进动效"]

    def test_score_out_of_range_rejected(self) -> None:
        assert self.gen._parse_critique(json.dumps({"score": 120})) is None
        assert self.gen._parse_critique(json.dumps({"score": -1})) is None

    def test_score_unparseable_rejected(self) -> None:
        assert self.gen._parse_critique(json.dumps({"score": "高"})) is None
        assert self.gen._parse_critique("garbage") is None
        assert self.gen._parse_critique("") is None


class TestIssuesFixable:
    """_issues_fixable 判定测试。"""

    def test_suggestions_always_fixable(self) -> None:
        assert MGGenerator._issues_fixable(["严重问题"], ["加粒子"]) is True
        assert MGGenerator._issues_fixable(["无法实现"], ["换方案"]) is True

    def test_empty_issues_not_fixable(self) -> None:
        assert MGGenerator._issues_fixable([], []) is False

    def test_unfixable_marker(self) -> None:
        assert MGGenerator._issues_fixable(["需求信息不足，无法实现"], []) is False

    def test_plain_issue_fixable(self) -> None:
        assert MGGenerator._issues_fixable(["元素过少"], []) is True
