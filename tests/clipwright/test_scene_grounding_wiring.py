"""R8 接线测试：方案接地真的接进了 Agent（而不是只在服务层可用）。

为什么单独有这个文件：R1–R7 反复验证过"服务层正确但没接线"是最隐蔽的一类缺陷
（`tool/vision.py` 的假分就是活例子）。所以每一轮都要额外断言"Agent 真的会用它"。

覆盖三条接线：

1. `StructureAgent` 排场景前注入索引摘要、排完场景后给每个场景钉引用；
2. `RequirementsAgent.translate_scenes` 把引用透传到 `raw_scenes` 并提供接地摘要；
3. `EditAgent` 把引用写进 `clip.metadata`，让"这一刀用了哪个镜头"可回查。
"""

from __future__ import annotations

from typing import Any

import pytest

from clipwright.schema.content import (
    ContentIndex,
    ShotRecord,
    ShotVisual,
    format_shot_ref,
)

SHA = "d" * 64


def _index() -> ContentIndex:
    return ContentIndex(
        sha256=SHA, asset_path="f.mp4", filename="f.mp4",
        shots=[ShotRecord(index=0, t0=0.0, t1=3.0, duration_sec=3.0,
                          visual=ShotVisual(description="球场全景", tags=["足球"])),
               ShotRecord(index=1, t0=3.0, t1=6.0, duration_sec=3.0,
                          visual=ShotVisual(description="球员射门进球", tags=["足球", "进球"]))])


class _Ctx:
    """最小的 AgentContext 替身（只带 extra_params，够用且不依赖真实上下文）。"""

    def __init__(self, **extra: Any) -> None:
        self.extra_params = dict(extra)
        self.topic = "测试选题"
        self.pipeline_id = "p_r8"


@pytest.fixture()
def with_index(monkeypatch):
    """让 `load_project_indexes` 返回构造索引（不打桩检索层本身）。"""
    from clipwright.services import scene_grounding as sg

    monkeypatch.setattr(sg, "load_project_indexes", lambda pid: [_index()])


class TestStructureAgentWiring:
    def test_ground_scenes_attaches_refs(self, with_index) -> None:
        from clipwright.agents.structure_agent import StructureAgent

        agent = StructureAgent.__new__(StructureAgent)
        scenes = [{"title": "进球", "description": "球员射门进球", "duration_sec": 5}]
        out, notes = agent._ground_scenes(scenes, _Ctx(project_id="p1"), [])
        assert out[0]["material_refs"], out
        assert any("接地" in n for n in notes)

    def test_ground_scenes_degrades_without_index(self, monkeypatch) -> None:
        """没有索引时**原样返回**（不产生任何 material_refs 字段）。"""
        from clipwright.agents.structure_agent import StructureAgent
        from clipwright.services import scene_grounding as sg

        monkeypatch.setattr(sg, "load_project_indexes", lambda pid: [])
        agent = StructureAgent.__new__(StructureAgent)
        scenes = [{"title": "进球", "description": "球员射门进球", "duration_sec": 5}]
        out, notes = agent._ground_scenes(scenes, _Ctx(), [])
        assert out == scenes and notes == []
        assert "material_refs" not in out[0]

    def test_ground_scenes_survives_index_failure(self, monkeypatch) -> None:
        from clipwright.agents.structure_agent import StructureAgent
        from clipwright.services import scene_grounding as sg

        monkeypatch.setattr(sg, "load_project_indexes", lambda pid: (_ for _ in ()).throw(RuntimeError("x")))
        agent = StructureAgent.__new__(StructureAgent)
        scenes = [{"title": "a", "description": "b", "duration_sec": 3}]
        out, _notes = agent._ground_scenes(scenes, _Ctx(), [])
        assert out == scenes

    def test_index_context_empty_without_indexes(self, monkeypatch) -> None:
        from clipwright.agents.structure_agent import StructureAgent
        from clipwright.services import content_query

        monkeypatch.setattr(content_query, "index_context_text", lambda pid, **kw: "")
        assert StructureAgent._index_context(_Ctx()) == ""

    def test_index_context_never_raises(self, monkeypatch) -> None:
        from clipwright.agents.structure_agent import StructureAgent
        from clipwright.services import content_query

        def boom(*a, **kw):
            raise RuntimeError("index broken")

        monkeypatch.setattr(content_query, "index_context_text", boom)
        assert StructureAgent._index_context(_Ctx()) == ""

    def test_confirmed_refs_are_not_overwritten(self, with_index) -> None:
        """用户确认过的引用必须保留（复用路径不能拿检索结果覆盖人工指定）。"""
        from clipwright.agents.structure_agent import StructureAgent

        agent = StructureAgent.__new__(StructureAgent)
        keep = format_shot_ref(SHA, 0)
        scenes = [{"title": "进球", "description": "球员射门进球",
                   "material_refs": [keep], "duration_sec": 5}]
        out, _notes = agent._ground_scenes(scenes, _Ctx(project_id="p1"), [])
        assert out[0]["material_refs"] == [keep]


class TestRequirementsAgentWiring:
    @pytest.mark.asyncio
    async def test_translate_scenes_passes_refs_through(self) -> None:
        from clipwright.agents.requirements_agent import RequirementsAgent

        agent = RequirementsAgent.__new__(RequirementsAgent)

        class _LLM:
            last_usage = None

            async def structured_output(self, **kw):
                return {"markdown_content": "# 规划书"}

        agent._llm = _LLM()
        ref = format_shot_ref(SHA, 1)
        scenes = [{"title": "进球", "description": "射门", "duration_sec": 5,
                   "material_refs": [ref]}]
        result = await agent.translate_scenes(scenes, {}, pipeline_id="p")
        assert result["raw_scenes"][0]["material_refs"] == [ref]
        assert ref in result["material_summary"]

    @pytest.mark.asyncio
    async def test_translate_scenes_fallback_keeps_refs(self) -> None:
        from clipwright.agents.requirements_agent import RequirementsAgent

        agent = RequirementsAgent.__new__(RequirementsAgent)

        class _BadLLM:
            last_usage = None

            async def structured_output(self, **kw):
                raise RuntimeError("llm down")

        agent._llm = _BadLLM()
        ref = format_shot_ref(SHA, 0)
        scenes = [{"title": "开场", "description": "球场", "duration_sec": 5,
                   "material_refs": [ref]}]
        result = await agent.translate_scenes(scenes, {}, pipeline_id="p")
        assert result["raw_scenes"][0]["material_refs"] == [ref]     # 降级路径不丢引用

    def test_default_markdown_shows_refs_and_gaps(self) -> None:
        from clipwright.agents.requirements_agent import RequirementsAgent

        md = RequirementsAgent._default_markdown([
            {"title": "进球", "duration_sec": 5, "description": "射门",
             "material_refs": [format_shot_ref(SHA, 1)]},
            {"title": "美食", "duration_sec": 5, "description": "面", "material_gap": "库里没有"},
        ])
        assert "素材引用" in md and format_shot_ref(SHA, 1) in md
        assert "素材缺口" in md

    def test_material_summary_empty_without_grounding(self) -> None:
        from clipwright.agents.requirements_agent import RequirementsAgent

        assert RequirementsAgent._material_summary([{"title": "x"}]) == ""


class TestEditAgentWiring:
    def test_clip_metadata_helper_round_trip(self) -> None:
        """EditAgent 用的转换函数：场景 → clip.metadata（引用可回查）。"""
        from clipwright.services.scene_grounding import refs_to_clip_metadata

        ref = format_shot_ref(SHA, 1)
        meta = refs_to_clip_metadata({"title": "进球", "material_refs": [ref]})
        assert meta["material_refs"] == [ref]

    def test_edit_agent_imports_grounding_helper(self) -> None:
        """接线自检：EditAgent 里确实把接地 metadata 合并进了 clip.metadata。

        这里只能做源码级检查（真正的合并发生在 1000+ 行的 `execute` 里，构造
        完整输入成本过高）：断言"调用了转换函数"且"确实把返回值逐键写进 metadata"。
        这是防"服务层写好了但没人用"的兜底——R1–R7 反复踩过这个坑。
        """
        import inspect

        from clipwright.agents import edit_agent

        src = inspect.getsource(edit_agent)
        assert "refs_to_clip_metadata" in src
        assert "for key, value in refs_to_clip_metadata(scene_obj).items()" in src
