"""动画 LLM 化（M1–M3）测试：类型规格、提示词装配、类型门禁、Agent 路由与去重。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clipwright.animation import type_specs as TS
from clipwright.animation.mg import type_generation as TG
from clipwright.animation.type_specs import (
    AnimationTypeSpec,
    CategoryAnimationContext,
    PersonaAnimationContext,
    get_spec,
)
from clipwright.agents.animation_agent import AnimationAgent
from clipwright.animation.catalog import AnimationCatalog
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


# ── M1：类型规格与提示词 ─────────────────────────────────────

def test_registry_covers_every_catalog_type():
    catalog_ids = {str(i["id"]) for i in AnimationCatalog.get_logic_animations()}
    assert catalog_ids, "catalog 应返回逻辑动画"
    assert catalog_ids == set(TS.known_type_ids())


def test_every_type_has_prompt_file_and_registry_is_clean():
    assert TS.missing_prompt_files() == []
    assert TS.validate_registry() == []


def test_type_prompt_is_structured_and_versioned():
    body = TS.load_type_prompt("comparison")
    for section in ("Intent", "Semantic constraints", "Composition", "Motion", "Anti-patterns"):
        assert body.get(section), f"comparison 缺 {section} 段"
    version = TS.prompt_version_of("comparison")
    assert len(version) == 12
    # 同一文件稳定
    assert version == TS.prompt_version_of("comparison")


def test_spec_intent_is_type_specific_not_generic():
    comparison = get_spec("comparison")
    causation = get_spec("causation")
    assert comparison and causation
    assert comparison.intent != causation.intent
    assert comparison.fallback_preset == "comparison"
    assert causation.fallback_preset == "causation"


def test_persona_fingerprint_stable_and_ignores_empty():
    a = PersonaAnimationContext(persona_id="Zam", palette=("#000", "#fff"))
    b = PersonaAnimationContext(persona_id="Zam", palette=("#000", "#fff"))
    c = PersonaAnimationContext(persona_id="Zam", palette=("#000", "#ff0000"))
    assert TS.persona_fingerprint(a) == TS.persona_fingerprint(b)
    assert TS.persona_fingerprint(a) != TS.persona_fingerprint(c)


# ── M2：提示词装配与类型门禁 ────────────────────────────────

def test_build_type_description_carries_type_category_persona_and_duration():
    spec = get_spec("comparison")
    desc = TG.build_type_description(
        spec,
        "已装实例一个都没坏 vs 有一半人说没多大事",
        6.0,
        CategoryAnimationContext(plugin_id="knowledge_longform", tone="克制"),
        PersonaAnimationContext(persona_id="Zam", palette=("#FF0000",), motion_speed="slow"),
        prior_fingerprints=["causation@12s"],
    )
    assert "comparison" in desc and spec.intent in desc
    assert "knowledge_longform" in desc and "克制" in desc
    assert "Zam" in desc and "#FF0000" in desc
    assert "6.00" in desc, "必须把片段时长写进提示词"
    assert "causation@12s" in desc, "同片上下文必须注入，避免重复"
    assert "只输出 MGDef JSON" in desc


def _moving_def(duration: float = 6.0) -> dict:
    return {
        "duration_sec": duration,
        "elements": [
            {"type": "text", "content": "{left}", "keyframes": [
                {"time": 0, "opacity": 0, "translate_x": -24},
                {"time": duration, "opacity": 1, "translate_x": 0},
            ]},
            {"type": "text", "content": "{right}", "keyframes": [
                {"time": 0, "opacity": 0, "translate_y": 18},
                {"time": duration, "opacity": 1, "translate_y": 0},
            ]},
        ],
    }


def test_motion_report_detects_real_motion():
    report = TG.motion_report(_moving_def(), 6.0)
    assert report["ok"] is True
    assert report["moving_count"] >= 2


def test_motion_report_flags_static_animation():
    frozen = {
        "duration_sec": 6.0,
        "elements": [
            {"type": "text", "content": "{a}", "keyframes": [
                {"time": 0, "opacity": 1}, {"time": 6, "opacity": 1}]},
            {"type": "shape", "shape": "rect", "keyframes": [
                {"time": 0, "opacity": 1}, {"time": 6, "opacity": 1}]},
        ],
    }
    assert TG.motion_report(frozen, 6.0)["ok"] is False


def test_motion_report_counts_fade_in_out_as_motion():
    # 淡入→停留→淡出：首末都接近 0，只比首末会误判静止（实测误伤 3 个片段）
    mg_def = {
        "duration_sec": 6.0,
        "elements": [
            {"type": "text", "content": "{a}", "keyframes": [
                {"time": 0, "opacity": 0}, {"time": 1, "opacity": 1},
                {"time": 5, "opacity": 1}, {"time": 6, "opacity": 0}]},
            {"type": "text", "content": "{b}", "keyframes": [
                {"time": 0, "translate_x": -30}, {"time": 1, "translate_x": 0},
                {"time": 6, "translate_x": 0}]},
        ],
    }
    report = TG.motion_report(mg_def, 6.0)
    assert report["ok"] is True
    assert report["moving_count"] == 2


def test_validate_for_type_blocks_static_and_passes_moving():
    spec = get_spec("comparison")
    assert TG.validate_for_type(_moving_def(), spec, 6.0) == []
    issues = TG.validate_for_type({"duration_sec": 6.0, "elements": [
        {"type": "text", "content": "{a}", "keyframes": [
            {"time": 0, "opacity": 1}, {"time": 6, "opacity": 1}]},
    ]}, spec, 6.0)
    assert any("元素数量" in i for i in issues)
    assert any("近乎静止" in i for i in issues)


def test_normalize_duration_clamps_keyframes():
    mg_def = {
        "duration_sec": 0,
        "elements": [{"type": "text", "content": "{a}", "keyframes": [
            {"time": 0}, {"time": 99}]}],
    }
    out = TG.normalize_duration(mg_def, 5.0)
    assert out["duration_sec"] == 5.0
    assert out["elements"][0]["keyframes"][-1]["time"] == 5.0


def test_fingerprint_normalizes_punctuation_and_space():
    a = TG.fingerprint_of("comparison", "已装实例一个都没坏 vs 有一半人说没多大事")
    b = TG.fingerprint_of("comparison", "已装实例一个都没坏VS有一半人说没多大事！")
    c = TG.fingerprint_of("causation", "已装实例一个都没坏 vs 有一半人说没多大事")
    assert a == b
    assert a != c


# ── M2：generate_for_type 复用生成器 + 门禁 ─────────────────

def _result(**over):
    base = {
        "success": True,
        "html": "<div class='mg-el'>x</div>",
        "mg_def": _moving_def(),
        "method": "llm",
        "params": {"left": "A", "right": "B"},
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_generate_for_type_reuses_generator_and_reports_motion():
    spec = get_spec("comparison")
    gen = MagicMock()
    gen.generate = AsyncMock(return_value=_result())
    gen._render_html_no_residuals = AsyncMock(return_value="<div>rerendered</div>")

    res = await TG.generate_for_type(
        gen, spec=spec, content="A vs B", duration_s=6.0,
        category=CategoryAnimationContext(plugin_id="knowledge_longform"),
        persona=PersonaAnimationContext(persona_id="Zam"),
    )

    assert res.ok is True
    assert res.type_id == "comparison"
    assert res.prompt_version
    assert res.motion["ok"] is True
    kwargs = gen.generate.await_args.kwargs
    assert kwargs["description"].count("comparison") >= 1
    assert kwargs["text_content"] == "A vs B"


@pytest.mark.asyncio
async def test_generate_for_type_blocks_static_output():
    spec = get_spec("comparison")
    frozen = {
        "duration_sec": 6.0,
        "elements": [
            {"type": "text", "content": "{a}", "keyframes": [{"time": 0, "opacity": 1}, {"time": 6, "opacity": 1}]},
            {"type": "text", "content": "{b}", "keyframes": [{"time": 0, "opacity": 1}, {"time": 6, "opacity": 1}]},
        ],
    }
    gen = MagicMock()
    gen.generate = AsyncMock(return_value=_result(mg_def=frozen))
    gen._render_html_no_residuals = AsyncMock(return_value="<div>still</div>")

    res = await TG.generate_for_type(gen, spec=spec, content="A vs B", duration_s=6.0)

    assert res.ok is False
    assert any("近乎静止" in i for i in res.issues)


@pytest.mark.asyncio
async def test_generate_for_type_survives_generator_exception():
    spec = get_spec("causation")
    gen = MagicMock()
    gen.generate = AsyncMock(side_effect=RuntimeError("provider down"))

    res = await TG.generate_for_type(gen, spec=spec, content="A→B", duration_s=5.0)

    assert res.ok is False
    assert res.issues and "provider down" in res.issues[0]


# ── M3：Agent 主路径（类型化生成 → 失败回退 → 同片去重）───────

def _agent() -> AnimationAgent:
    agent = AnimationAgent()
    agent._pid = "p_typed"
    agent._tl_width, agent._tl_height, agent._tl_fps = 1920, 1080, 30.0
    agent._mg_category_context = {"plugin_id": "knowledge_longform", "display_name": "知识区长片"}
    agent._persona_prompt = "冷色调高对比，逐字硬切"
    agent._vision_prompt = ""
    return agent


def _anim_track() -> Track:
    tl = Timeline()
    track = Track(id="t_anim", name="动画轨", kind=ClipKind.ANIMATION, index=5, clips=[])
    tl.tracks.append(track)
    return track


def _vid_clip(start: float = 10.0) -> Clip:
    return Clip(
        id=f"v_{start}", kind=ClipKind.VIDEO, asset_id="a.mp4", track_id="v_main",
        start_sec=start, duration_sec=6.0,
    )


def _ok_type_result():
    return TG.TypeGenerationResult(
        ok=True,
        mg_def=_moving_def(),
        html="<div>typed</div>",
        method="llm",
        type_id="comparison",
        prompt_version="abc123456789",
        motion={"moving_count": 2, "ok": True},
        raw={"params": {"left": "A"}},
    )


@pytest.mark.asyncio
async def test_logic_animation_uses_typed_generation_primary_path():
    agent = _agent()
    agent._hyperframes_available = AsyncMock(return_value=True)
    track = _anim_track()

    fake_gen = MagicMock()
    fake_gen.generate_for_type = AsyncMock(return_value=_ok_type_result())

    with patch("clipwright.animation.mg.generator.MGGenerator", return_value=fake_gen):
        await agent._handle_logic_animation(
            track, _vid_clip(), "comparison", "对比", {"text": "A vs B"}, None,
        )

    assert len(track.clips) == 1
    clip = track.clips[0]
    assert clip.metadata["renderer"] == "mg_hyperframes"
    assert clip.metadata["anim_type"] == "comparison"
    assert clip.metadata["type_prompt_version"] == "abc123456789"
    assert clip.metadata["logic_category"] == "logic"
    assert fake_gen.generate_for_type.await_count == 1


@pytest.mark.asyncio
async def test_same_content_animation_is_deduplicated_in_one_film():
    agent = _agent()
    agent._hyperframes_available = AsyncMock(return_value=True)
    track = _anim_track()
    fake_gen = MagicMock()
    fake_gen.generate_for_type = AsyncMock(return_value=_ok_type_result())

    with patch("clipwright.animation.mg.generator.MGGenerator", return_value=fake_gen):
        await agent._handle_logic_animation(track, _vid_clip(10.0), "comparison", "对比", {"text": "A vs B"}, None)
        await agent._handle_logic_animation(track, _vid_clip(30.0), "comparison", "对比", {"text": "A vs B"}, None)

    assert len(track.clips) == 1, "同内容只播一次"
    assert fake_gen.generate_for_type.await_count == 1, "重复内容不应再调 LLM"


@pytest.mark.asyncio
async def test_typed_failure_falls_back_to_svg():
    agent = _agent()
    agent._hyperframes_available = AsyncMock(return_value=True)
    track = _anim_track()
    fake_gen = MagicMock()
    fake_gen.generate_for_type = AsyncMock(
        return_value=TG.TypeGenerationResult(ok=False, type_id="diagram",
                                             issues=["生成器未返回可用产物"])
    )
    fallback = AsyncMock()

    with patch("clipwright.animation.mg.generator.MGGenerator", return_value=fake_gen):
        with patch.object(agent, "_handle_svg_fallback", fallback):
            await agent._handle_logic_animation(track, _vid_clip(), "diagram", "箭头", {"text": "A→B"}, None)

    fallback.assert_awaited_once()


@pytest.mark.asyncio
async def test_switch_off_restores_legacy_path():
    from clipwright.config import settings

    agent = _agent()
    track = _anim_track()
    fallback = AsyncMock()
    with patch.object(settings, "animation_llm_generation", False):
        with patch.object(agent, "_handle_svg_fallback", fallback):
            await agent._handle_logic_animation(track, _vid_clip(), "diagram", "箭头", {"text": "A→B"}, None)
    fallback.assert_awaited_once()


# ── M3b：SVG 回退必须是"动"的 ───────────────────────────────

def test_diagram_svg_fallback_carries_real_animation():
    import re

    from clipwright.animation.diagram_svg import DiagramRenderer, DiagramStyle

    svg = DiagramRenderer.render(
        {"preset": "causation", "title": "A → B", "items": ["甲", "乙"], "duration_sec": 6},
        DiagramStyle(), 1920, 1080,
    )
    assert "<style>" in svg and "@keyframes ds-rise" in svg
    assert svg.count("animation:ds-") >= 2, "元素必须带真实 animation-name"
    assert re.search(r'<g style="animation-delay:[0-9.]+s"', svg) is None, "不能只剩无效的 animation-delay"
    assert "ds-draw" in svg, "连线要有描线动画"
