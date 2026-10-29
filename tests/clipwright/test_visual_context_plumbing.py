"""PRD §16/§17 修复的回归测试：视觉上下文装配、背景守卫、参数语义映射、可读性门禁。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.animation.mg import type_generation as TG
from clipwright.animation.mg.generator import MGGenerator
from clipwright.animation.type_specs import get_spec
from clipwright.schema.agent import AgentContext
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services import stage_inputs
from clipwright.services import visual_context as VC


# ── R1：VisualContext 三级装配 ─────────────────────────────

def test_visual_context_prefers_board_then_extra_then_store():
    board = {"persona_prompt": "BOARD", "visual_config": {"palette": "BOARD-PALETTE"}}
    extra = {"persona_prompt": "EXTRA", "visual_config": {"palette": "EXTRA-PALETTE"}}
    vc = VC.load("Zam", board=board, extra=extra)
    assert vc.persona_prompt == "BOARD"
    assert vc.visual_config["palette"] == "BOARD-PALETTE"
    assert "board" in vc.source


def test_visual_context_falls_back_to_persona_store():
    vc = VC.load("Zam", board={}, extra={})
    assert vc.source == "persona_store"
    assert len(vc.persona_prompt) > 100, "应读到 persona prompt.md / manifest.prompt"
    assert vc.visual_config.get("palette"), "应读到 persona visual.palette"
    assert vc.rhythm.get("cut_profile"), "应读到 persona rhythm.cut_profile"


def test_visual_context_truncates_to_configured_limit(monkeypatch):
    from clipwright.config import settings

    monkeypatch.setattr(settings, "persona_prompt_max_chars", 300)
    vc = VC.load("Zam", board={"persona_prompt": "x" * 5000})
    assert len(vc.persona_prompt) == 300
    assert vc.prompt_chars_raw == 5000
    assert vc.to_trace()["truncated"] is True


def test_visual_context_fingerprint_is_stable_and_persona_specific():
    a = VC.load("Zam", board={"visual_config": {"palette": "P1"}})
    b = VC.load("Zam", board={"visual_config": {"palette": "P1"}})
    c = VC.load("Zam", board={"visual_config": {"palette": "P2"}})
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint


def test_apply_rhythm_defaults_fills_missing_only():
    vc = VC.load("Zam", board={}, extra={})
    extra = {"cut_profile": "显式值"}
    out = VC.apply_rhythm_defaults(extra, vc)
    assert out["cut_profile"] == "显式值", "不得覆盖调用方显式值"
    assert out.get("cut_density_tier"), "空缺键应被 persona rhythm 补齐"


# ── R3：stage_inputs 装配（persona 真的进 Input）────────────

def _fake_task(**extra):
    request = SimpleNamespace(
        persona_id="Zam",
        category_plugin_id="knowledge_longform",
        topic="测试主题",
        extra=extra or {},
    )
    return SimpleNamespace(id="t_plumbing", request=request)


def _timeline() -> Timeline:
    tl = Timeline()
    tl.tracks.append(Track(id="v", name="视频轨", kind=ClipKind.VIDEO, index=0, clips=[
        Clip(id="c1", kind=ClipKind.VIDEO, asset_id="a.mp4", track_id="v",
             start_sec=0, duration_sec=6),
    ]))
    tl.tracks.append(Track(id="a", name="动画轨", kind=ClipKind.ANIMATION, index=5, clips=[]))
    return tl


def test_animation_input_gets_persona_from_store_even_without_board():
    tl = _timeline()
    ctx = AgentContext(pipeline_id="p", persona_id="Zam",
                       category_plugin_id="knowledge_longform", topic="t", extra_params={})
    inp = stage_inputs.assemble("animation", task=_fake_task(), board={"timeline": tl}, context=ctx)
    assert inp.persona_prompt, "persona_prompt 不得为空（旧实现恒空）"
    assert inp.visual_config.get("palette"), "visual_config 应带 persona palette"
    assert "persona" in str(inp.persona_prompt).lower() or len(inp.persona_prompt) > 100


def test_structure_input_gets_persona_and_vision_from_extra():
    ctx = AgentContext(pipeline_id="p", persona_id="Zam",
                       category_plugin_id="knowledge_longform", topic="t",
                       extra_params={"vision_prompt": "要冷色调背景板"})
    inp = stage_inputs.assemble("structure", task=_fake_task(), board={}, context=ctx)
    assert inp.persona_prompt and inp.persona_config
    assert inp.vision_prompt == "要冷色调背景板", "vision_prompt 应能从 extra 透传（PRD D7）"


def test_material_and_audio_and_quality_configs_come_from_extra():
    ctx = AgentContext(
        pipeline_id="p", persona_id="Zam", category_plugin_id="knowledge_longform", topic="t",
        extra_params={
            "material_plugin_config": {"sources": ["pexels"]},
            "audio_config": {"bgm_volume": 0.4},
            "constraints": {"max_duration_sec": 720},
        },
    )
    tl = _timeline()
    mat = stage_inputs.assemble("material", task=_fake_task(),
                               board={"script_skeleton": {"scenes": []}}, context=ctx)
    assert mat.material_plugin_config == {"sources": ["pexels"]}, "D8 死入口应接线"
    aud = stage_inputs.assemble("audio", task=_fake_task(), board={"timeline": tl}, context=ctx)
    assert aud.audio_config == {"bgm_volume": 0.4}, "D9 死入口应接线"
    qa = stage_inputs.assemble("quality", task=_fake_task(), board={"timeline": tl}, context=ctx)
    assert qa.constraints.get("max_duration_sec") == 720 or qa.constraints, "D10 应能拿到约束"


# ── R4/D7：色板不再被硬编码覆盖 + 半透明底板保留 ────────────

def test_background_guard_keeps_scrim_but_drops_opaque():
    scrim = {"style": {}, "elements": [{"type": "bg", "background": "rgba(0,0,0,0.45)"}]}
    assert MGGenerator._ensure_no_background(scrim, "")["elements"][0]["background"] == "rgba(0,0,0,0.45)"

    opaque = {"style": {}, "elements": [{"type": "bg", "background": "#000000"}]}
    assert MGGenerator._ensure_no_background(opaque, "")["elements"][0]["background"] == "transparent"


def test_background_guard_respects_allow_scrim_switch():
    scrim = {"style": {}, "elements": [{"type": "bg", "background": "rgba(0,0,0,0.45)"}]}
    out = MGGenerator._ensure_no_background(scrim, "", allow_scrim=False)
    assert out["elements"][0]["background"] == "transparent"


def test_background_guard_keeps_everything_when_vision_prompt_present():
    mg = {"style": {"background": "#101010"}, "elements": [{"type": "bg", "background": "#000"}]}
    out = MGGenerator._ensure_no_background(mg, "要暗底")
    assert out["style"]["background"] == "#101010"


@pytest.mark.asyncio
async def test_resolve_style_no_longer_injects_hardcoded_palette():
    from clipwright.agents.animation_agent import AnimationAgent

    agent = AnimationAgent()
    agent._vision_prompt = ""
    style = await agent._resolve_style({"palette": "冷色调+高对比"}, {})
    # 旧实现会无条件写死 #000000/#FFFFFF/#FF0000；现在必须有来源标注，且不冒充 persona
    assert style.get("palette_source") in (None, "persona", "fallback")
    if style.get("palette_source") == "fallback":
        assert not style.get("primary_color") or style.get("primary_color") != "#000000"


# ── D12：参数按语义映射，不再同文 ───────────────────────────

def test_llm_params_map_by_semantics_not_position():
    mg_def = {"params": {"label": {}, "q1": {}, "q2": {}}, "elements": []}
    params = MGGenerator._build_llm_params(mg_def, "无通知|功能关闭|无整改要求", {})
    assert params["label"] != params["q1"]
    assert len({params["label"], params["q1"], params["q2"]}) == 3


# ── 可读性门禁（PRD §17 D7 配套）────────────────────────────

def test_readability_issue_flags_bare_text_without_shadow_or_scrim():
    spec = get_spec("comparison")
    mg_def = {
        "duration_sec": 6.0,
        "elements": [
            {"type": "text", "content": "{a}", "keyframes": [
                {"time": 0, "opacity": 0, "translate_x": -30},
                {"time": 6, "opacity": 1, "translate_x": 0}]},
            {"type": "text", "content": "{b}", "keyframes": [
                {"time": 0, "opacity": 0, "translate_y": 20},
                {"time": 6, "opacity": 1, "translate_y": 0}]},
        ],
    }
    issues = TG.validate_for_type(mg_def, spec, 6.0)
    assert any("可读性风险" in i for i in issues)

    # 加半透明底板后不应再报
    mg_def["elements"].append({"type": "bg", "background": "rgba(0,0,0,0.4)"})
    issues2 = TG.validate_for_type(mg_def, spec, 6.0)
    assert not any("可读性风险" in i for i in issues2)

# ── 闭环项：可读性硬门禁 / coverage / 缓存键 / shot_params ──

def test_ensure_readability_adds_shadow_only_to_bare_text():
    mg = {"duration_sec": 6.0, "elements": [
        {"type": "text", "content": "{a}", "keyframes": [
            {"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]},
        {"type": "text", "content": "{b}", "text_shadow": "0 2px 4px #000", "keyframes": [
            {"time": 0, "translate_y": 10}, {"time": 1, "translate_y": 0}]},
    ]}
    out, fixes = TG.ensure_readability(mg)
    assert fixes == ["text#0"]
    assert out["elements"][0]["text_shadow"]
    assert out["elements"][1]["text_shadow"] == "0 2px 4px #000"


def test_motion_coverage_measures_last_actual_change():
    mg = {"duration_sec": 6.0, "elements": [
        {"type": "text", "content": "{a}", "keyframes": [
            {"time": 0, "translate_x": -40}, {"time": 1.2, "translate_x": 0},
            {"time": 6, "translate_x": 0}]},
        {"type": "text", "content": "{b}", "keyframes": [
            {"time": 0, "translate_y": 20}, {"time": 1.0, "translate_y": 0},
            {"time": 6, "translate_y": 0}]},
    ]}
    report = TG.motion_report(mg, 6.0)
    assert 0.15 <= report["coverage"] <= 0.25, "coverage 应按最后一次实际变化计算，而不是末帧时间"


def test_validate_flags_motion_limited_to_entrance():
    spec = get_spec("comparison")
    mg = {"duration_sec": 6.0, "elements": [
        {"type": "text", "content": "{a}", "text_shadow": "0 2px 4px #000", "keyframes": [
            {"time": 0, "opacity": 0}, {"time": 0.8, "opacity": 1}, {"time": 6, "opacity": 1}]},
        {"type": "text", "content": "{b}", "text_shadow": "0 2px 4px #000", "keyframes": [
            {"time": 0, "opacity": 0}, {"time": 0.9, "opacity": 1}, {"time": 6, "opacity": 1}]},
    ]}
    issues = TG.validate_for_type(mg, spec, 6.0)
    assert any("动效集中在入场阶段" in i for i in issues)


def test_cache_key_differs_by_persona_fingerprint():
    from clipwright.animation.mg.generator import _mg_cache_key

    a = _mg_cache_key("desc", "text", {"persona_fingerprint": "aaa"})
    b = _mg_cache_key("desc", "text", {"persona_fingerprint": "bbb"})
    assert a != b, "不同 persona 不得命中同一条生成缓存"


def test_shot_params_filled_from_category_plugin(monkeypatch):
    # 真实运行里 category 插件由 load-all 注册；单测用假插件验证接线逻辑本身
    class _FakeCategory:
        def get_shot_params(self, _payload):
            return {"shot_len_sec": 4.5, "cut_density": "中高"}

    from clipwright.category.registry import CategoryRegistry

    monkeypatch.setattr(CategoryRegistry, "get", classmethod(lambda cls, _pid: _FakeCategory()))
    ctx = AgentContext(pipeline_id="p", persona_id="Zam",
                       category_plugin_id="knowledge_longform", topic="t", extra_params={})
    stage_inputs.assemble("animation", task=_fake_task(), board={"timeline": _timeline()}, context=ctx)
    assert ctx.extra_params.get("shot_params") == {"shot_len_sec": 4.5, "cut_density": "中高"}


def test_rhythm_defaults_written_back_to_context():
    ctx = AgentContext(pipeline_id="p", persona_id="Zam",
                       category_plugin_id="knowledge_longform", topic="t", extra_params={})
    stage_inputs.assemble("animation", task=_fake_task(), board={"timeline": _timeline()}, context=ctx)
    assert ctx.extra_params.get("cut_profile"), "persona rhythm 应回写到 ctx.extra_params"
