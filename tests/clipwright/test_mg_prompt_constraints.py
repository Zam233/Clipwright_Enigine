# -*- coding: utf-8 -*-
"""A3 + A8 MG 提示词编排约束 / 批判检查项 / 图片语义匹配测试。

覆盖计划 ux-polish todo 23（A3）与 todo 28（A8）：
- A3(a)：mg_dynamic 生成 system prompt（_call_llm 注入，位于 _STRICT_JSON_OUTPUT 之前）
  含编排约束关键词：顺序揭示 / 每节拍一个主运动 / 方向强化语义 / 构图居中 /
  连线止于节点边缘不穿入 / 箭头方向随流程 / 次级元素错峰（stagger）。
- A3(b)：_critique_quality 批判 prompt 含构图/层级/方向检查项。
- A8：含 image 元素时批判增加「图片与场景语义匹配」检查（基于场景标题/关键词）；
  图片语义低分 → 触发 _call_llm_critique_repair，修复提示词要求换图或回退无图动画。

对抗类：malformed_input（LLM 返回非法 JSON → 既有降级路径不被破坏）、
misleading_success_output（断言真实 prompt 文本与检查项，而非仅调用次数）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clipwright.animation.mg.generator import MGGenerator


def _valid_def() -> dict:
    """通过 validator 且可渲染的合法 MG 定义（无 image 元素）。"""
    return {
        "animation_id": "mg_generated_prompt_constraints",
        "name": "约束测试",
        "description": "流程动画",
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


def _valid_def_with_image() -> dict:
    """含 image 元素的 mg_def（A8 检查路径专用；validator 尚未支持 image 类型，
    因此只在批判/修复单元路径使用，不经 generate() 全链路）。"""
    d = _valid_def()
    d["animation_id"] = "mg_generated_with_image"
    d["elements"].append(
        {
            "type": "image",
            "src": "assets/graphene.png",
            "x": 200,
            "y": 150,
            "width": 320,
            "height": 240,
            "keyframes": [
                {"time": 0, "opacity": 0},
                {"time": 0.6, "opacity": 1},
            ],
        }
    )
    return d


def _resp(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


class _CaptureLLM:
    """固定响应 + 记录每次调用的 kwargs（真实 prompt 文本）。"""

    def __init__(self, content: str) -> None:
        self.calls: list[dict] = []
        self._content = content

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return _resp(self._content)


class _SeqLLM:
    """按顺序弹出响应（生成 → 批判），并记录调用。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return _resp(self.responses.pop(0))


class _GarbageLLM:
    """对抗 malformed_input：LLM 返回无法解析的内容。"""

    async def generate(self, **kwargs):
        return _resp("this is not json {{{")


# ── A3(a)：生成 system prompt 编排约束注入 ────────────────────────

class TestGenerationPromptConstraints:
    """mg_dynamic 生成 prompt（_call_llm）必须包含编排约束关键词。"""

    @pytest.mark.asyncio
    async def test_generation_system_prompt_contains_orchestration_constraints(self) -> None:
        gen = MGGenerator()
        fake = _CaptureLLM(json.dumps(_valid_def(), ensure_ascii=False))
        gen._llm = fake
        result = await gen._call_llm("流程动画", "A|B|C", {}, {}, {})
        assert result is not None
        assert len(fake.calls) == 1
        system = fake.calls[0]["messages"][0]["content"]

        # 顺序揭示（sequential reveal）
        assert "顺序揭示" in system
        assert "sequential reveal" in system
        # 每节拍一个主运动（one primary motion per beat）
        assert "主运动" in system
        assert "one primary motion per beat" in system
        # 方向强化语义（direction reinforces semantics）
        assert "方向强化语义" in system
        # 构图居中（composition centered）
        assert "构图居中" in system
        # 连线止于节点边缘、不得穿入节点内部（connectors stop at node edge）
        assert "节点边缘" in system
        assert "不得穿入节点内部" in system
        # 箭头方向随流程（arrow direction follows flow）
        assert "箭头方向随流程" in system
        # 次级元素错峰 stagger（stagger secondary elements）
        assert "错峰" in system
        assert "stagger" in system

    @pytest.mark.asyncio
    async def test_constraints_injected_before_strict_json_output(self) -> None:
        """注入位置：编排约束必须位于 _STRICT_JSON_OUTPUT（输出硬性要求）之前。"""
        gen = MGGenerator()
        fake = _CaptureLLM(json.dumps(_valid_def(), ensure_ascii=False))
        gen._llm = fake
        await gen._call_llm("流程动画", "A|B|C", {}, {}, {})
        system = fake.calls[0]["messages"][0]["content"]
        assert system.index("动画编排约束") < system.index("输出硬性要求")
        assert system.index("顺序揭示") < system.index("输出硬性要求")

    @pytest.mark.asyncio
    async def test_generate_end_to_end_prompt_contains_constraints(self) -> None:
        """端到端：注入约束后 generate() 正常走通（method=llm，2 次 LLM 调用）。"""
        gen = MGGenerator()
        fake = _SeqLLM([
            json.dumps(_valid_def(), ensure_ascii=False),
            json.dumps({"score": 90, "issues": [], "suggestions": []}),
        ])
        gen._llm = fake
        result = await gen.generate("流程动画", "A|B|C")
        assert result["success"] is True
        assert result["method"] == "llm"
        assert len(fake.calls) == 2
        system = fake.calls[0]["messages"][0]["content"]
        for keyword in (
            "顺序揭示", "主运动", "方向强化语义", "构图居中",
            "节点边缘", "箭头方向随流程", "错峰",
        ):
            assert keyword in system, f"生成 prompt 缺少约束关键词: {keyword}"


# ── A3(b)：批判 prompt 构图/层级/方向检查项 ──────────────────────

class TestCritiquePromptCheckItems:
    """_critique_quality 批判 prompt 必须包含构图/层级/方向检查项。"""

    @pytest.mark.asyncio
    async def test_critique_prompt_includes_composition_hierarchy_direction(self) -> None:
        gen = MGGenerator()
        fake = _CaptureLLM(
            json.dumps({"score": 80, "issues": [], "suggestions": []})
        )
        gen._llm = fake
        result = await gen._critique_quality(_valid_def(), "流程动画", {}, {})
        assert result == {"score": 80, "issues": [], "suggestions": []}
        system = fake.calls[0]["messages"][0]["content"]
        assert "评审检查项" in system
        assert "构图" in system
        assert "层级" in system
        assert "方向" in system

    @pytest.mark.asyncio
    async def test_critique_prompt_without_image_skips_semantic_check(self) -> None:
        """无 image 元素 → 不注入图片语义匹配检查（保持既有行为）。"""
        gen = MGGenerator()
        fake = _CaptureLLM(
            json.dumps({"score": 80, "issues": [], "suggestions": []})
        )
        gen._llm = fake
        await gen._critique_quality(_valid_def(), "流程动画", {}, {})
        system = fake.calls[0]["messages"][0]["content"]
        assert "语义匹配" not in system
        # 构图/层级/方向检查项在无图时仍存在
        assert "构图" in system


# ── A8：图片语义匹配批判检查 ─────────────────────────────────────

class TestImageSemanticCritique:
    """含 image 元素时，批判 prompt 检查图片与场景的语义匹配。"""

    @pytest.mark.asyncio
    async def test_critique_prompt_checks_image_semantic_match_with_scene(self) -> None:
        gen = MGGenerator()
        fake = _CaptureLLM(
            json.dumps({
                "score": 35,
                "issues": ["image 元素与场景语义不匹配"],
                "suggestions": ["更换为语义匹配的图片"],
            })
        )
        gen._llm = fake
        scene = {"title": "石墨烯材料", "keywords": ["石墨烯", "材料科学"]}
        result = await gen._critique_quality(
            _valid_def_with_image(), "石墨烯材料介绍", {}, {}, scene_context=scene,
        )
        assert result is not None
        assert result["score"] == 35
        system = fake.calls[0]["messages"][0]["content"]
        assert "图片语义匹配检查" in system
        assert "语义匹配" in system
        assert "更换为语义匹配的图片" in system
        # 场景上下文注入作为匹配依据（标题/关键词进入 user prompt）
        user = fake.calls[0]["messages"][1]["content"]
        assert "石墨烯材料" in user
        assert "石墨烯" in user


# ── A8：图片语义低分 → 修复触发（换图或回退无图动画）─────────────

class TestImageMismatchRepair:
    """图片语义低分触发 _call_llm_critique_repair，结果为无图动画。"""

    @pytest.mark.asyncio
    async def test_image_mismatch_low_score_triggers_repair_fallback_no_image(
        self, monkeypatch,
    ) -> None:
        g = MGGenerator()
        state = {"repair_calls": 0, "seen_mg_def": None,
                 "seen_critique": None, "seen_scene": None}

        async def fake_critique(mg_def, description, persona_style,
                                category_context, vision_prompt="", scene_context=None):
            return {
                "score": 30,
                "issues": ["image 元素与场景语义不匹配"],
                "suggestions": ["更换为语义匹配图片，或移除 image 元素"],
            }

        async def fake_repair(mg_def, critique, description, text_content,
                              persona_style, scene_context, category_context,
                              vision_prompt=""):
            state["repair_calls"] += 1
            state["seen_mg_def"] = mg_def
            state["seen_critique"] = critique
            state["seen_scene"] = scene_context
            # 回退无图动画：移除全部 image 元素
            no_image = dict(mg_def)
            no_image["elements"] = [e for e in mg_def["elements"]
                                    if e.get("type") != "image"]
            no_image["animation_id"] = "mg_generated_no_image"
            return no_image

        monkeypatch.setattr(g, "_critique_quality", fake_critique)
        monkeypatch.setattr(g, "_call_llm_critique_repair", fake_repair)

        result = await g._finalize_with_critique(
            _valid_def_with_image(), "llm", "石墨烯材料介绍", "石墨烯",
            {}, {"title": "石墨烯材料", "keywords": ["石墨烯"]}, {},
            width=1920, height=1080, fps=30.0,
        )
        assert result is not None
        # 低分（图片语义不匹配）→ 修复被触发，且携带原始含图定义 + 批判反馈 + 场景上下文
        assert state["repair_calls"] == 1
        assert any(e.get("type") == "image"
                   for e in state["seen_mg_def"]["elements"])
        assert "语义" in "".join(state["seen_critique"].get("issues", []))
        assert state["seen_scene"]["title"] == "石墨烯材料"
        # 结果：换图/回退路径产出无图动画（method=critique_repair）
        assert result["method"] == "critique_repair"
        assert not any(e.get("type") == "image"
                       for e in result["mg_def"]["elements"])

    @pytest.mark.asyncio
    async def test_critique_repair_prompt_requests_swap_or_no_image_fallback(
        self,
    ) -> None:
        """修复提示词要求换图或回退无图动画（真实 prompt 文本断言）。"""
        gen = MGGenerator()
        fake = _CaptureLLM(json.dumps(_valid_def(), ensure_ascii=False))
        gen._llm = fake
        critique = {
            "score": 30,
            "issues": ["image 元素与场景语义不匹配"],
            "suggestions": ["更换为语义匹配的图片"],
        }
        repaired = await gen._call_llm_critique_repair(
            _valid_def_with_image(), critique, "石墨烯材料介绍", "石墨烯",
            {}, {"title": "石墨烯材料"}, {}, "",
        )
        assert repaired is not None
        # 修复结果回退为无图动画
        assert not any(e.get("type") == "image" for e in repaired["elements"])
        system = fake.calls[0]["messages"][0]["content"]
        assert "图片修复指引" in system
        assert "更换 image 元素的 src" in system
        assert "回退为纯矢量动画" in system

    def test_has_image_elements_detection(self) -> None:
        """_has_image_elements 前置判断。"""
        assert MGGenerator._has_image_elements(_valid_def_with_image()) is True
        assert MGGenerator._has_image_elements(_valid_def()) is False
        assert MGGenerator._has_image_elements({}) is False
        assert MGGenerator._has_image_elements({"elements": "not-list"}) is False


# ── 对抗类：malformed_input → 既有降级路径不受破坏 ───────────────

class TestMalformedInputFallbackPreserved:
    """LLM 返回非法 JSON 时仍走既有 fallback（A3/A8 不改变降级行为）。"""

    @pytest.mark.asyncio
    async def test_malformed_json_still_falls_back(self, monkeypatch) -> None:
        g = MGGenerator()
        fallback_hits = {"n": 0}

        async def fake_fallback(*args, **kwargs):
            fallback_hits["n"] += 1
            return {"success": False, "method": "fallback", "html": "",
                    "mg_def": {}, "fallback_template": None, "generation_id": "fake"}

        monkeypatch.setattr(g, "_llm", _GarbageLLM())
        monkeypatch.setattr(g, "_fallback_generate", fake_fallback)
        result = await g.generate("流程动画", "A|B")
        assert fallback_hits["n"] == 1
        assert result["method"] == "fallback"
        assert result["success"] is False
