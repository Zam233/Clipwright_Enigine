"""StructureAgent 开场动画标记按视频类型分支（A5：_strip_opening_animation_markers 类型感知）。

- knowledge_longform 等知识类：开场仍剥离动画标记（既有行为保持）；
- kichiku_fastcut 等快节奏类型（_OPENING_ANIMATION_ALLOWED_PLUGIN_IDS）：开场保留动画标记。
"""

from __future__ import annotations

import pytest

from clipwright.agents.structure_agent import (
    _OPENING_ANIMATION_ALLOWED_PLUGIN_IDS,
    StructureAgent,
)
from clipwright.schema.agent import AgentContext, AgentDecision, StructureInput

_MG_MARKER = (
    '[逻辑动画]mg_dynamic:{"description":"三阶段增长柱状图",'
    '"text":"2023|2024|2025","style":"tech_dark"}'
)


def _agent() -> StructureAgent:
    return StructureAgent()


def _scenes() -> list[dict]:
    return [
        {
            "title": "开场",
            "description": f"开场口播 {_MG_MARKER}",
            "duration_sec": 10,
            "keywords": ["a"],
        },
        {
            "title": "中段",
            "description": f"中段论证 {_MG_MARKER}",
            "duration_sec": 10,
            "keywords": ["b"],
        },
    ]


class TestStripOpeningMarkersTypeAware:
    """_strip_opening_animation_markers 按视频类型分支（真实分支，非 mock）。"""

    def test_knowledge_longform_still_strips_opening_markers(self) -> None:
        """知识长片（knowledge_longform）：开场动画标记仍被剥离（既有行为保持）。"""
        out = _agent()._strip_opening_animation_markers(_scenes(), "knowledge_longform")
        assert "[逻辑动画]" not in out[0]["description"]
        assert "开场口播" in out[0]["description"]
        # 第二个场景不受影响
        assert "[逻辑动画]" in out[1]["description"]

    @pytest.mark.parametrize("plugin_id", sorted(_OPENING_ANIMATION_ALLOWED_PLUGIN_IDS))
    def test_fast_pace_types_preserve_opening_markers(self, plugin_id: str) -> None:
        """快节奏类型（kichiku_fastcut 等）：开场动画标记被保留。"""
        out = _agent()._strip_opening_animation_markers(_scenes(), plugin_id)
        assert "[逻辑动画]" in out[0]["description"]
        assert "开场口播" in out[0]["description"]
        assert "[逻辑动画]" in out[1]["description"]

    def test_empty_plugin_id_keeps_legacy_strip(self) -> None:
        """未指定类型（空 plugin_id）：维持原有剥离行为（向后兼容）。"""
        out = _agent()._strip_opening_animation_markers(_scenes(), "")
        assert "[逻辑动画]" not in out[0]["description"]

    def test_unknown_plugin_id_keeps_legacy_strip(self) -> None:
        """未知类型：保守剥离（与既有行为一致）。"""
        out = _agent()._strip_opening_animation_markers(_scenes(), "unknown_type_x")
        assert "[逻辑动画]" not in out[0]["description"]

    def test_kichiku_fastcut_preserves_text_animation_marker(self) -> None:
        """快节奏类型：[文字动画] 标记同样保留（不止 [逻辑动画]）。"""
        scenes = [
            {
                "title": "开场",
                "description": "高能开场 [文字动画]弹幕大字：来了",
                "duration_sec": 10,
                "keywords": ["a"],
            },
        ]
        out = _agent()._strip_opening_animation_markers(scenes, "kichiku_fastcut")
        assert "[文字动画]" in out[0]["description"]

    def test_knowledge_longform_strips_text_animation_marker(self) -> None:
        """知识长片：[文字动画] 标记同样被剥离。"""
        scenes = [
            {
                "title": "开场",
                "description": "开场口播 [文字动画]强调：概念",
                "duration_sec": 10,
                "keywords": ["a"],
            },
        ]
        out = _agent()._strip_opening_animation_markers(scenes, "knowledge_longform")
        assert "[文字动画]" not in out[0]["description"]

    def test_fast_pace_scene_without_marker_untouched(self) -> None:
        """快节奏类型开场无标记 → description 原样返回。"""
        scenes = [
            {
                "title": "开场",
                "description": "纯口播开场",
                "duration_sec": 10,
                "keywords": ["a"],
            },
        ]
        out = _agent()._strip_opening_animation_markers(scenes, "kichiku_fastcut")
        assert out[0]["description"] == "纯口播开场"


class TestStripOpeningMarkersWiring:
    """execute 调用点把 context.category_plugin_id 传入剥离方法（端到端接线）。"""

    async def test_execute_fast_pace_keeps_opening_marker(self) -> None:
        """复用规划书路径：kichiku_fastcut → 开场标记保留（真实 execute 分支）。"""
        agent = _agent()

        async def _noop_enrich(scenes, ctx):
            return scenes

        agent._enrich_scene_animations = _noop_enrich  # type: ignore[assignment]
        inp = StructureInput(
            context=AgentContext(
                pipeline_id="p_a5_fast",
                persona_id="persona_x",
                category_plugin_id="kichiku_fastcut",
                topic="鬼畜剪辑",
            ),
            production_plan={
                "raw_scenes": [
                    {
                        "title": "开场",
                        "description": f"高能开场 {_MG_MARKER}",
                        "duration_sec": 10,
                        "keywords": ["a"],
                    },
                ],
            },
        )
        out = await agent.execute(inp, inp.context)
        assert out.decision == AgentDecision.PASS
        assert "[逻辑动画]" in out.scenes[0]["description"]

    async def test_execute_knowledge_longform_strips_opening_marker(self) -> None:
        """复用规划书路径：knowledge_longform → 开场标记被剥离（既有行为保持）。"""
        agent = _agent()

        async def _noop_enrich(scenes, ctx):
            return scenes

        agent._enrich_scene_animations = _noop_enrich  # type: ignore[assignment]
        inp = StructureInput(
            context=AgentContext(
                pipeline_id="p_a5_know",
                persona_id="persona_x",
                category_plugin_id="knowledge_longform",
                topic="知识讲解",
            ),
            production_plan={
                "raw_scenes": [
                    {
                        "title": "开场",
                        "description": f"开场口播 {_MG_MARKER}",
                        "duration_sec": 10,
                        "keywords": ["a"],
                    },
                ],
            },
        )
        out = await agent.execute(inp, inp.context)
        assert out.decision == AgentDecision.PASS
        assert "[逻辑动画]" not in out.scenes[0]["description"]
