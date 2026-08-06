"""A7 — 动画 Agent 图片素材库 + VisionService 语义索引测试（计划 ux-polish todo 27）。

覆盖：
(a) AnimationInput 接受 image_assets: list[dict]（{path, tags, description}，schema/agent.py）
(b) animation_agent 对图片资产调用 VisionService.analyze_image 构建语义索引
    {path, tags, description}；分析失败回退文件名标签
(c) 语义索引注入 mg_dynamic 生成 prompt（"可用图片列表（含语义描述），
    选择语义匹配的图片放入动画 image 元素"），让 LLM 主动选图（防 📡→石墨烯 类错配）
(d) 生成结果可含 image 元素（prompt 驱动）

对抗类：
- malformed_input：非 dict / 缺 path / 空 path 资产跳过；分析抛异常回退文件名标签
- misleading_success_output：断言真实 prompt 文本包含图片语义列表与选图指令
- dirty_worktree：本任务不产生任何 git 提交
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.schema.agent import AgentContext, AgentDecision, AnimationInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track

_MG_MARKER = (
    '[逻辑动画]mg_dynamic:{"description":"科技场景展示",'
    '"text":"芯片|算力","style":"tech_dark"}'
)

# Manual-QA channel：真实 prompt 文本中的固定文案
_IMAGE_SECTION_HEADER = "可用图片列表（含语义描述）"
_IMAGE_SELECT_INSTRUCTION = "选择语义匹配的图片放入动画 image 元素"


def _ctx() -> AgentContext:
    return AgentContext(
        pipeline_id="p_a7_images",
        persona_id="persona_a7",
        category_plugin_id="cat_a7",
        topic="a7 image assets",
        extra_params={},
    )


def _timeline_with_mg_dynamic() -> Timeline:
    tl = Timeline()
    tl.tracks.append(
        Track(
            id="v_main",
            name="视频轨",
            kind=ClipKind.VIDEO,
            index=0,
            clips=[
                Clip(
                    id="clip_mg",
                    kind=ClipKind.VIDEO,
                    asset_id="a1",
                    track_id="v_main",
                    start_sec=0.0,
                    duration_sec=5.0,
                    metadata={"description": f"科技内容 {_MG_MARKER}"},
                ),
            ],
        )
    )
    return tl


def _anim_clips(out) -> list[Clip]:
    """从 execute 结果中取出动画轨 clip。"""
    assert out.timeline is not None
    for t in out.timeline.tracks:
        if str(t.kind) == ClipKind.ANIMATION.value:
            return list(t.clips or [])
    return []

class _FakeVisionService:
    """VisionService 桩：analyze_image 返回 per-path 结果，可配置失败路径。"""

    def __init__(self, results: dict | None = None, fail_paths: set[str] | None = None) -> None:
        self.results = dict(results or {})
        self.fail_paths = set(fail_paths or set())
        self.analyze_image = AsyncMock(side_effect=self._analyze)
        self.instances = 0

    def __call__(self) -> "_FakeVisionService":
        self.instances += 1
        return self

    def _analyze(self, path: str) -> dict:
        if path in self.fail_paths:
            raise RuntimeError(f"analyze failed: {path}")
        return self.results.get(path, {"tags": [], "description": ""})


class _FakeMGGenerator:
    """MGGenerator 桩：记录 generate 调用，返回含 image 元素的 mg_def。"""

    def __init__(self, mg_def: dict) -> None:
        self.generate = AsyncMock(
            return_value={
                "success": True,
                "html": "<div class='mg-anim'><img alt='pick'/></div>",
                "mg_def": mg_def,
                "method": "llm",
                "fallback_template": None,
                "generation_id": "gen_a7_test",
            }
        )

    def __call__(self) -> "_FakeMGGenerator":
        return self

    def last_description(self) -> str:
        """最后一次 generate 调用收到的 description（真实 prompt 内容）。"""
        calls = self.generate.await_args_list
        assert calls, "generate 未被调用"
        return calls[-1].kwargs.get("description", "")


def _mg_def_with_image(src: str) -> dict:
    return {
        "animation_id": "mg_generated_images",
        "duration_sec": 3.0,
        "width": 1920,
        "height": 1080,
        "elements": [
            {"type": "text", "content": "标题", "x": "center", "y": "top"},
            {"type": "image", "src": src, "x": "center", "y": "center",
             "width": 400, "height": 300},
        ],
    }


async def _run_execute(
    timeline: Timeline,
    image_assets: list,
    fake_vision: _FakeVisionService,
    fake_mg: _FakeMGGenerator,
):
    """以标准桩组合运行 AnimationAgent.execute（patch 必须在 await 期间保持生效）。"""
    agent = AnimationAgent()
    ctx = _ctx()
    inp = AnimationInput(context=ctx, timeline=timeline, image_assets=image_assets)
    with (
        patch.object(AnimationAgent, "_resolve_style", new=AsyncMock(return_value={})),
        patch("clipwright.services.vision.VisionService", fake_vision),
        patch("clipwright.animation.mg.MGGenerator", fake_mg),
    ):
        return await agent.execute(inp, ctx)


class TestAnimationInputSchema:
    """AnimationInput 接受 image_assets（schema/agent.py）。"""

    def test_accepts_image_assets(self) -> None:
        assets = [
            {"path": "/img/a.png", "tags": ["石墨烯"], "description": "石墨烯分子结构"},
            {"path": "/img/b.png", "tags": ["芯片"]},
        ]
        inp = AnimationInput(context=_ctx(), timeline=Timeline(), image_assets=assets)
        assert inp.image_assets == assets

    def test_default_empty_list(self) -> None:
        """省略 image_assets 时默认为空列表（现有调用方不破坏）。"""
        inp = AnimationInput(context=_ctx(), timeline=Timeline())
        assert inp.image_assets == []
class TestVisionSemanticIndex:
    """动画 Agent 对图片资产调用视觉分析器构建语义索引。"""

    @pytest.mark.asyncio
    async def test_analyzer_called_per_asset_and_index_injected(self) -> None:
        assets = [{"path": "/img/a.png"}, {"path": "/img/b.png"}]
        fake_vision = _FakeVisionService({
            "/img/a.png": {"tags": ["石墨烯", "材料"], "description": "石墨烯分子结构示意图"},
            "/img/b.png": {"tags": ["芯片"], "description": "芯片特写"},
        })
        fake_mg = _FakeMGGenerator(_mg_def_with_image("/img/a.png"))
        out = await _run_execute(_timeline_with_mg_dynamic(), assets, fake_vision, fake_mg)

        # (b) 分析器每个资产调用一次
        assert fake_vision.analyze_image.await_count == 2
        called = {c.args[0] for c in fake_vision.analyze_image.await_args_list}
        assert called == {"/img/a.png", "/img/b.png"}

        # (c) 语义索引注入 mg_dynamic 生成 prompt（真实 prompt 文本断言）
        desc = fake_mg.last_description()
        assert _IMAGE_SECTION_HEADER in desc
        assert _IMAGE_SELECT_INSTRUCTION in desc
        assert "/img/a.png" in desc and "/img/b.png" in desc
        assert "石墨烯分子结构示意图" in desc
        assert "芯片特写" in desc
        assert "石墨烯" in desc and "芯片" in desc
        # 原始动画需求保留（prompt 驱动，无回归）
        assert "科技场景展示" in desc

        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1

    @pytest.mark.asyncio
    async def test_prompt_uses_analyzer_description_over_input_tags(self) -> None:
        """分析器返回的语义描述优先注入 prompt（即使资产自带 tags）。"""
        assets = [{"path": "/img/g.png", "tags": ["旧标签"], "description": "旧描述"}]
        fake_vision = _FakeVisionService({
            "/img/g.png": {"tags": ["新标签"], "description": "新语义描述"},
        })
        fake_mg = _FakeMGGenerator(_mg_def_with_image("/img/g.png"))
        await _run_execute(_timeline_with_mg_dynamic(), assets, fake_vision, fake_mg)

        desc = fake_mg.last_description()
        assert "新语义描述" in desc
        assert "新标签" in desc
        assert "旧描述" not in desc

    @pytest.mark.asyncio
    async def test_analyzer_failure_falls_back_to_filename_tags(self) -> None:
        """分析抛异常 → 回退文件名标签（malformed_input 对抗）。"""
        assets = [{"path": "/img/graphene_molecule.png"}]
        fake_vision = _FakeVisionService(fail_paths={"/img/graphene_molecule.png"})
        fake_mg = _FakeMGGenerator(_mg_def_with_image("/img/graphene_molecule.png"))
        out = await _run_execute(_timeline_with_mg_dynamic(), assets, fake_vision, fake_mg)

        assert out.decision == AgentDecision.PASS
        desc = fake_mg.last_description()
        assert "graphene" in desc
        assert "molecule" in desc
        assert "文件: graphene_molecule.png" in desc
        # 文件名标签不得污染原始动画需求
        assert "科技场景展示" in desc

    @pytest.mark.asyncio
    async def test_malformed_assets_skipped(self) -> None:
        """坏资产 dict（缺 path / 空 path / 非字符串 path）跳过，不崩溃（malformed_input 对抗）。

        注：非 dict 元素（None/str）在 schema 层就被 pydantic 拒绝——这是正确的
        契约行为；此处覆盖的是 dict 形状但字段非法的资产，必须由 Agent 防御跳过。
        """
        assets = [
            {},
            {"src": ""},
            {"path": "   "},
            {"path": None},
            {"path": 123},
            {"path": "/img/ok.png"},
        ]
        fake_vision = _FakeVisionService({"/img/ok.png": {"tags": ["好"], "description": "可用图"}})
        fake_mg = _FakeMGGenerator(_mg_def_with_image("/img/ok.png"))
        out = await _run_execute(_timeline_with_mg_dynamic(), assets, fake_vision, fake_mg)

        assert fake_vision.analyze_image.await_count == 1
        assert fake_vision.analyze_image.await_args_list[0].args[0] == "/img/ok.png"
        assert out.decision == AgentDecision.PASS
        desc = fake_mg.last_description()
        assert "可用图" in desc
        assert "/img/ok.png" in desc
class TestImageElementsInResult:
    """生成结果可含 image 元素（prompt 驱动，防 📡→石墨烯 类错配）。"""

    @pytest.mark.asyncio
    async def test_result_may_contain_image_elements(self) -> None:
        assets = [{"path": "/img/chip.png"}]
        fake_vision = _FakeVisionService({
            "/img/chip.png": {"tags": ["芯片"], "description": "芯片特写"},
        })
        fake_mg = _FakeMGGenerator(_mg_def_with_image("/img/chip.png"))
        out = await _run_execute(_timeline_with_mg_dynamic(), assets, fake_vision, fake_mg)

        # prompt 驱动：LLM 依据选图指令在生成结果中放入 image 元素
        assert _IMAGE_SELECT_INSTRUCTION in fake_mg.last_description()
        clips = _anim_clips(out)
        assert clips, "应创建动画 clip"
        mg_def = (clips[0].metadata or {}).get("mg_def", {})
        elements = mg_def.get("elements", [])
        images = [e for e in elements if isinstance(e, dict) and e.get("type") == "image"]
        assert images, "mg_def 应含 image 元素"
        assert images[0]["src"] == "/img/chip.png"
        assert out.generated_mg_count == 1


class TestNoImageAssetsRegression:
    """无 image_assets 时行为与之前完全一致（现有动画测试必须通过）。"""

    @pytest.mark.asyncio
    async def test_no_assets_no_analyzer_prompt_unchanged(self) -> None:
        class _BoomVision:
            def __init__(self, *a, **k):
                raise AssertionError("无 image_assets 时不应实例化 VisionService")

        fake_mg = _FakeMGGenerator(_mg_def_with_image("/never/used.png"))
        agent = AnimationAgent()
        ctx = _ctx()
        inp = AnimationInput(context=ctx, timeline=_timeline_with_mg_dynamic())
        with (
            patch.object(AnimationAgent, "_resolve_style", new=AsyncMock(return_value={})),
            patch("clipwright.services.vision.VisionService", _BoomVision),
            patch("clipwright.animation.mg.MGGenerator", fake_mg),
        ):
            out = await agent.execute(inp, ctx)

        assert out.decision == AgentDecision.PASS
        assert out.generated_mg_count == 1
        # prompt 与原始一致：不注入任何图片段落
        assert fake_mg.last_description() == "科技场景展示"
        clips = _anim_clips(out)
        assert clips and (clips[0].metadata or {}).get("anim_type") == "mg_dynamic"


class TestPromptFormatterUnit:
    """_format_image_index_prompt 输出真实 prompt 文本（Manual-QA channel）。"""

    def test_prompt_contains_semantic_list_and_instruction(self) -> None:
        section = AnimationAgent._format_image_index_prompt([
            {"path": "a.png", "tags": ["石墨烯", "材料"], "description": "石墨烯分子结构示意图"},
        ])
        assert _IMAGE_SECTION_HEADER in section
        assert _IMAGE_SELECT_INSTRUCTION in section
        assert "a.png" in section
        assert "石墨烯、材料" in section
        assert "石墨烯分子结构示意图" in section

    def test_prompt_empty_index(self) -> None:
        section = AnimationAgent._format_image_index_prompt([])
        assert _IMAGE_SECTION_HEADER in section
        assert _IMAGE_SELECT_INSTRUCTION in section

    def test_filename_tags_from_path(self) -> None:
        assert AnimationAgent._filename_tags("graphene_molecule_v2.png") == ["graphene", "molecule"]
        assert AnimationAgent._filename_tags("ab.png") == []
