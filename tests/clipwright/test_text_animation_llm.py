# -*- coding: utf-8 -*-
"""文字动画 LLM 化 M1（PRD text-animation-llm-2026-10-30）。

背景：文字动画此前查 `AnimationCatalog` 固定关键帧表、默认 position=bottom，
与字幕同区同文 → 成片底部两行字。这里锁住新契约：类型注册表 + 六段提示词 +
LLM 生成 MGDef + 文字专属校验（字幕带避让/行宽/元素数）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from clipwright.animation import text_generation as tg
from clipwright.animation.text_specs import (
    PROMPT_SECTIONS,
    TEXT_ANIM_SPECS,
    get_text_spec,
    known_text_ids,
    load_text_prompt,
    missing_text_prompts,
    validate_text_registry,
)


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _LLM:
    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = []

    async def generate(self, messages, **kw):  # noqa: ANN001, ANN003
        self.calls.append({"messages": messages, **kw})
        return _Resp(self._contents.pop(0) if self._contents else "")


def _valid_def(duration=3.0, **over):
    data = {
        "animation_id": "text_emphasis",
        "duration_sec": duration,
        "width": 1920,
        "height": 1080,
        "elements": [{
            "id": "t0", "type": "text", "content": "没有监管要求",
            "font_size": 64, "color": "#F5C518", "text_shadow": "0 2px 8px rgba(0,0,0,.6)",
            "position": "center",
            "keyframes": [
                {"time": 0.0, "opacity": 0, "scale": 0.88},
                {"time": 0.35, "opacity": 1, "scale": 1.0, "easing": "back-out"},
            ],
        }],
    }
    data.update(over)
    return data


# ── 注册表与提示词 ─────────────────────────────────────────

def test_six_types_registered():
    ids = known_text_ids()
    assert len(ids) == 6
    assert {"emphasis_keyword", "reveal_line", "quote_block",
            "label_callout", "number_punch", "title_stack"} == set(ids)


def test_registry_prompts_are_complete():
    assert missing_text_prompts() == []
    assert validate_text_registry() == []
    for spec in TEXT_ANIM_SPECS:
        sections = load_text_prompt(spec.id)
        for name in PROMPT_SECTIONS:
            assert sections.get(name), f"{spec.id} 缺 {name}"


def test_prompt_carries_caption_avoidance_and_limits():
    spec = get_text_spec("emphasis_keyword")
    system, user = tg.build_text_prompt(
        spec, text="这不是在回应什么监管要求", duration_s=3.0,
        persona={"accent_color": "#F5C518"}, caption_band=True,
    )
    assert "字幕带" in user and "70%" in user
    assert "禁止与字幕逐字相同" in user
    assert f"≤{tg.MAX_LINE_CHARS} 字" in user or str(tg.MAX_LINE_CHARS) in user
    assert "#F5C518" in user
    assert "MGDef JSON" in system


# ── 生成 ───────────────────────────────────────────────────

def test_generate_returns_valid_mg_def():
    llm = _LLM([json.dumps(_valid_def(), ensure_ascii=False)])
    out = asyncio.run(tg.generate_for_type(
        "emphasis_keyword", text="这不是在回应什么监管要求", duration_s=3.0, llm=llm))
    assert out.ok is True and out.method == "text_llm"
    assert out.mg_def["text_meta"]["type_id"] == "emphasis_keyword"
    assert out.mg_def["text_meta"]["avoid_caption_band_px"] == tg.CAPTION_BAND_PX
    assert out.prompt_version


def test_long_text_is_trimmed_to_line_limit():
    data = _valid_def()
    data["elements"][0]["content"] = "这是一句明显超过十四个字的旁白整句文本"
    llm = _LLM([json.dumps(data, ensure_ascii=False)])
    out = asyncio.run(tg.generate_for_type(
        "emphasis_keyword", text="x", duration_s=3.0, llm=llm))
    assert out.ok is True
    assert len(out.mg_def["elements"][0]["content"]) <= tg.MAX_LINE_CHARS


def test_bottom_position_is_rejected_then_repaired():
    data = _valid_def()
    data["elements"][0]["position"] = "bottom"
    bad = json.dumps(data, ensure_ascii=False)
    good = json.dumps(_valid_def(), ensure_ascii=False)
    llm = _LLM([bad, good])
    out = asyncio.run(tg.generate_for_type(
        "emphasis_keyword", text="x", duration_s=3.0, llm=llm))
    # 第一次被拒（bottom 压字幕带）→ 第二次通过
    assert len(llm.calls) == 2
    assert out.ok is True


def test_unparseable_output_falls_back():
    llm = _LLM(["这不是 JSON", "还是不是"])
    out = asyncio.run(tg.generate_for_type(
        "reveal_line", text="x", duration_s=3.0, llm=llm))
    assert out.ok is False and out.method == "text_failed"
    assert "不可解析" in out.error or "不可解析" in (out.error or "")


def test_unknown_type_rejected():
    out = asyncio.run(tg.generate_for_type("nope", text="x", duration_s=3.0, llm=_LLM([])))
    assert out.ok is False and "未注册" in out.error


def test_validate_text_def_flags_caption_band_and_overlong():
    data = _valid_def()
    data["elements"][0]["y"] = 1000
    data["elements"][0]["content"] = "超" * 20
    errors = tg.validate_text_def(data, duration_s=3.0)
    assert any("字幕带" in e for e in errors)
    assert any("字超过" in e for e in errors)

# ── 真实 LLM 反复换字段名：归一化必须兜住（E2E 2026-10-30 实测） ──────────

def _spec():
    from clipwright.animation.text_specs import get_text_spec

    return get_text_spec("emphasis_keyword") or get_text_spec("reveal_line")


def test_missing_content_key_is_normalized_not_failed():
    from clipwright.animation import text_generation as tg

    spec = _spec()
    mg = {"elements": [
        {"type": "text", "title": "没有监管要求", "keyframes": [{"time": 0, "opacity": 0}]},
    ]}
    out = tg._normalize(mg, spec, 3.0)
    assert out["elements"][0]["content"] == "没有监管要求"


def test_keyframe_without_time_is_spread_across_duration():
    """没有 time 的关键帧全部落到 0 会算出 0% 动效覆盖 → 最后被记成弱动效。"""
    from clipwright.animation import text_generation as tg

    spec = _spec()
    mg = {"elements": [
        {"type": "text", "content": "四小时", "keyframes": [
            {"opacity": 0, "scale": 0.8}, {"opacity": 1, "scale": 1.0}]},
    ]}
    out = tg._normalize(mg, spec, 3.0)
    times = [kf["time"] for kf in out["elements"][0]["keyframes"]]
    assert times == [0.0, 3.0], times


def test_alternate_keyframe_keys_are_accepted():
    from clipwright.animation import text_generation as tg

    spec = _spec()
    mg = {"elements": [
        {"type": "text", "content": "四小时", "frames": [
            {"t": 0.0, "opacity": 0}, {"at": 0.6, "opacity": 1}, {"ts": 2.4, "opacity": 1}]},
    ]}
    out = tg._normalize(mg, spec, 3.0)
    times = [kf["time"] for kf in out["elements"][0]["keyframes"]]
    assert times == [0.0, 0.6, 2.4]


def test_text_element_without_text_is_dropped():
    from clipwright.animation import text_generation as tg

    spec = _spec()
    mg = {"elements": [
        {"type": "text", "keyframes": [{"time": 0, "opacity": 1}]},
        {"type": "text", "content": "留着我", "keyframes": [{"time": 0, "opacity": 1},
                                                          {"time": 1.0, "opacity": 1}]},
    ]}
    out = tg._normalize(mg, spec, 3.0)
    assert [e["content"] for e in out["elements"]] == ["留着我"]
