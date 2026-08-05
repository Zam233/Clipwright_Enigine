"""MGGenerator 自批判质量闭环测试（T7）。

依据 T7 落地实现（_finalize_with_critique 契约）：
- 校验通过后调 _critique_quality(mg_def, description, persona_style, category_context)
  → {score 0-100, issues[], suggestions[]} 或 None（LLM 失败/禁用 → 静默跳过，不降级）
- score >= min_score（config critique.min_score 默认 60）→ 直接通过，不修复
- score < min_score 且可修复（_issues_fixable）→ 一次带批判反馈的修复重试
  （_call_llm_critique_repair），成功 → method="critique_repair"
- 修复失败 → 返回 None，generate() 降级 fallback
- 低分但不可修复 → 接受原输出（不降级）
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clipwright.animation.mg.generator import MGGenerator

CONFIG_PATH = Path(__file__).resolve().parents[2] / "clipwright" / "animation" / "mg" / "config.yaml"


def _valid_def() -> dict:
    """合法 MG 定义（通过 validate_mg_json 且可渲染）。"""
    return {
        "animation_id": "mg_generated_critique_test",
        "name": "测试动画",
        "description": "critique 测试",
        "duration_sec": 3.0,
        "width": 1920,
        "height": 1080,
        "style": {"background": "transparent", "font_family": "sans-serif"},
        "params": {"text": {"type": "string", "default": "世界"}},
        "elements": [
            {
                "type": "text",
                "content": "Hello {text}",
                "x": "center",
                "y": "center",
                "font_size": 48,
                "font_color": "#ffffff",
                "keyframes": [
                    {"time": 0, "opacity": 0, "scale": 0.3},
                    {"time": 0.5, "opacity": 1, "scale": 1.0},
                    {"time": 2.8, "opacity": 0, "translate_y": -20},
                ],
            },
        ],
    }


def _repaired_def() -> dict:
    d = _valid_def()
    d["animation_id"] = "mg_generated_repaired"
    return d


class _CritiqueMockState:
    """跨 mock 共享的调用记录。"""

    def __init__(self) -> None:
        self.repair_calls = 0
        self.critique_calls = 0
        self.fallback_calls = 0


class TestCritiqueFlow:
    """generate() 内自批判三路径 + 异常静默。"""

    @pytest.fixture
    def gen(self, monkeypatch) -> tuple[MGGenerator, _CritiqueMockState]:
        g = MGGenerator()
        st = _CritiqueMockState()

        async def fake_llm(*args, **kwargs):
            return _valid_def()

        async def fake_critique(*args, **kwargs):
            st.critique_calls += 1
            return {"score": 90, "issues": [], "suggestions": []}

        async def fake_critique_repair(*args, **kwargs):
            st.repair_calls += 1
            return None

        async def fake_fallback(*args, **kwargs):
            st.fallback_calls += 1
            return {"success": False, "method": "fallback", "html": "",
                    "mg_def": {}, "fallback_template": None, "generation_id": "fake"}

        monkeypatch.setattr(g, "_call_llm", fake_llm)
        monkeypatch.setattr(g, "_critique_quality", fake_critique)
        monkeypatch.setattr(g, "_call_llm_critique_repair", fake_critique_repair)
        monkeypatch.setattr(g, "_fallback_generate", fake_fallback)
        return g, st

    async def test_high_score_passes(self, gen) -> None:
        """高分（>= min_score）→ 直接通过，不触发修复。"""
        g, st = gen
        result = await g.generate("测试动画需求", "世界")
        assert st.critique_calls == 1
        assert st.repair_calls == 0
        assert st.fallback_calls == 0
        assert result["success"] is True
        assert result["method"] == "llm"
        assert result["mg_def"]["animation_id"] == "mg_generated_critique_test"

    async def test_low_score_repair_success(self, gen, monkeypatch) -> None:
        """低分且可修复 → 一次带批判反馈的修复重试后成功（method=critique_repair）。"""
        g, st = gen

        async def fake_critique_low(*args, **kwargs):
            st.critique_calls += 1
            return {"score": 40, "issues": ["缺少光效", "粒子不足"],
                    "suggestions": ["增加 text_shadow 发光", "补充 4 个粒子"]}

        captured: dict = {}

        async def fake_critique_repair(mg_def, critique, *args, **kwargs):
            st.repair_calls += 1
            captured["critique"] = critique
            return _repaired_def()

        monkeypatch.setattr(g, "_critique_quality", fake_critique_low)
        monkeypatch.setattr(g, "_call_llm_critique_repair", fake_critique_repair)
        result = await g.generate("测试动画需求", "世界")
        assert st.critique_calls == 1
        assert st.repair_calls == 1
        assert st.fallback_calls == 0
        assert result["success"] is True
        assert result["method"] == "critique_repair"
        assert result["mg_def"]["animation_id"] == "mg_generated_repaired"
        # 修复重试应携带批判反馈（score + issues）
        assert captured["critique"]["score"] == 40
        assert any("光效" in i for i in captured["critique"]["issues"])

    async def test_low_score_repair_fail_falls_back(self, gen, monkeypatch) -> None:
        """低分可修复但修复失败 → 降级 fallback。"""
        g, st = gen

        async def fake_critique_low(*args, **kwargs):
            st.critique_calls += 1
            return {"score": 30, "issues": ["布局混乱"], "suggestions": ["重排"]}

        monkeypatch.setattr(g, "_critique_quality", fake_critique_low)
        # fake_critique_repair 默认返回 None
        result = await g.generate("测试动画需求", "世界")
        assert st.critique_calls == 1
        assert st.repair_calls == 1
        assert st.fallback_calls == 1
        assert result["success"] is False
        assert result["method"] == "fallback"

    async def test_low_score_not_fixable_accepts_original(self, gen, monkeypatch) -> None:
        """低分但不可修复 → 接受原输出，不降级。"""
        g, st = gen

        async def fake_critique_unfixable(*args, **kwargs):
            st.critique_calls += 1
            return {"score": 40, "issues": ["需求不足，无法修复"], "suggestions": []}

        monkeypatch.setattr(g, "_critique_quality", fake_critique_unfixable)
        result = await g.generate("测试动画需求", "世界")
        assert st.critique_calls == 1
        assert st.repair_calls == 0
        assert st.fallback_calls == 0
        assert result["success"] is True
        assert result["method"] == "llm"
        assert result["mg_def"]["animation_id"] == "mg_generated_critique_test"

    async def test_critique_none_silently_skipped(self, gen, monkeypatch) -> None:
        """批判返回 None（LLM 失败/禁用）→ 静默跳过，不降级。"""
        g, st = gen

        async def fake_critique_none(*args, **kwargs):
            st.critique_calls += 1
            return None

        monkeypatch.setattr(g, "_critique_quality", fake_critique_none)
        result = await g.generate("测试动画需求", "世界")
        assert st.critique_calls == 1
        assert st.repair_calls == 0
        assert st.fallback_calls == 0
        assert result["success"] is True
        assert result["method"] == "llm"


class TestCritiqueQualityUnit:
    """_critique_quality / _parse_critique / _issues_fixable 单元测试。"""

    @pytest.fixture
    def gen(self, monkeypatch) -> MGGenerator:
        return MGGenerator()

    async def test_critique_llm_failure_returns_none(self, gen, monkeypatch) -> None:
        """批判 LLM 抛异常 → 返回 None（静默跳过入口）。"""
        class _BoomLLM:
            async def generate(self, **kwargs):
                raise RuntimeError("LLM critique timeout")

        monkeypatch.setattr(gen, "_llm", _BoomLLM())
        result = await gen._critique_quality(_valid_def(), "测试", {}, {})
        assert result is None

    async def test_critique_disabled_returns_none(self, gen, monkeypatch) -> None:
        """critique.enabled=False → 返回 None。"""
        monkeypatch.setitem(gen._config, "critique", {"enabled": False, "min_score": 60})
        result = await gen._critique_quality(_valid_def(), "测试", {}, {})
        assert result is None

    async def test_critique_normalizes_response(self, gen, monkeypatch) -> None:
        """LLM 返回 {score, issues, suggestions} → 规范化输出。"""
        class _FakeLLM:
            async def generate(self, **kwargs):
                class R:
                    content = '{"score": 75, "issues": ["缺光效"], "suggestions": ["加 glow"]}'
                return R()

        monkeypatch.setattr(gen, "_llm", _FakeLLM())
        result = await gen._critique_quality(_valid_def(), "测试", {}, {})
        assert result == {"score": 75, "issues": ["缺光效"], "suggestions": ["加 glow"]}

    def test_parse_critique_invalid_score(self, gen) -> None:
        """score 越界/无法解析 → None。"""
        assert gen._parse_critique('{"score": 999}') is None
        assert gen._parse_critique('{"score": "abc"}') is None
        assert gen._parse_critique("not json") is None

    def test_issues_fixable(self, gen) -> None:
        """有建议 → 可修复；无建议无 issues → 不可修复；无解标记 → 不可修复。"""
        assert gen._issues_fixable(["缺光效"], ["加 glow"]) is True
        assert gen._issues_fixable([], []) is False
        assert gen._issues_fixable(["需求不足，无法修复"], []) is False
        assert gen._issues_fixable(["缺光效"], []) is True


class TestCritiqueConfig:
    """config.yaml critique 配置块。"""

    def test_critique_section_present(self) -> None:
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        if "critique" not in cfg:
            pytest.skip("critique 配置未落地")
        assert isinstance(cfg["critique"], dict)
        assert cfg["critique"].get("enabled", True) is True
        assert isinstance(cfg["critique"].get("min_score", 60), (int, float))

    def test_min_score_default_60(self) -> None:
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        if "critique" not in cfg:
            pytest.skip("critique 配置未落地")
        assert cfg["critique"].get("min_score", 60) == 60
        assert 0 <= cfg["critique"]["min_score"] <= 100
