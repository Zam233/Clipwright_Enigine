"""动画循环修复回归（2026-11-15 E2E）。

覆盖：
- 关键帧 x_offset/y_offset → translate_x/y 语义转换（不是删除）
- critique 专用解析：截断 JSON 不再被当成生成成功/失败
- 动画逐段落盘与复用：弱动效/模板回退不缓存
- replan 同一降级指纹限次，超限返回 exhausted（要求收尾写收据）
"""

from __future__ import annotations

import pytest

from clipwright.animation.mg.generator import MGGenerator
from clipwright.animation.mg.validator import repair_mg_json
from clipwright.agents.animation_agent import AnimationAgent
from clipwright.services import stage_closure


def _mg_def_with_offset() -> dict:
    return {
        "duration_sec": 3,
        "elements": [
            {
                "type": "text",
                "content": "x",
                "x": "center",
                "y": "center",
                "keyframes": [
                    {"time": 0, "opacity": 0, "y_offset": 40},
                    {"time": 1, "opacity": 1, "y_offset": 40},
                ],
            }
        ],
    }


def test_keyframe_offset_is_converted_not_deleted():
    repaired, fixes = repair_mg_json(_mg_def_with_offset())
    kfs = repaired["elements"][0]["keyframes"]
    assert "y_offset" not in kfs[0]
    assert kfs[0]["translate_y"] == 40
    assert any("converted" in f for f in fixes)


def test_critique_parser_rejects_truncated_json_without_generation_kind():
    truncated = '{"score": 52, "issues": ["JSON 不合规范：末尾 keyframes 被截断'
    parsed = MGGenerator._parse_critique_json(object.__new__(MGGenerator), truncated)
    assert parsed is None


def test_critique_parser_accepts_valid_json():
    valid = '{"score": 72, "issues": [], "suggestions": ["加一点动"]}'
    parsed = MGGenerator._parse_critique_json(object.__new__(MGGenerator), valid)
    assert parsed and parsed["score"] == 72


class _FakeResult:
    def __init__(self, *, issues=None, method="llm"):
        self.ok = True
        self.html = "<html>mg</html>"
        self.mg_def = {"duration_sec": 3, "elements": []}
        self.method = method
        self.issues = issues or []
        self.motion = {"moving_count": 2}
        self.raw = {"params": {}}


def _agent(tmp_path):
    agent = object.__new__(AnimationAgent)
    agent._ws_root = str(tmp_path)
    agent._tl_width = 1920
    agent._tl_height = 1080
    agent._find_overlapping_clip = lambda track, clip: None
    return agent


def test_animation_cache_roundtrip(tmp_path):
    agent = _agent(tmp_path)
    persona = {"persona_fingerprint": "p1"}
    agent._mg_cache_store("countdown", "文案", 3.0, persona, _FakeResult())
    hit = agent._mg_cache_lookup("countdown", "文案", 3.0, persona)
    assert hit is not None
    assert hit["quality_ok"] is True
    # 不同文本不命中
    assert agent._mg_cache_lookup("countdown", "另一段", 3.0, persona) is None


def test_weak_or_fallback_not_cached(tmp_path):
    agent = _agent(tmp_path)
    persona = {"persona_fingerprint": "p1"}
    agent._mg_cache_store("countdown", "弱动效", 3.0, persona,
                          _FakeResult(issues=["动画近乎静止"]))
    assert agent._mg_cache_lookup("countdown", "弱动效", 3.0, persona) is None

    agent._mg_cache_store("countdown", "模板", 3.0, persona,
                          _FakeResult(method="fallback_template"))
    assert agent._mg_cache_lookup("countdown", "模板", 3.0, persona) is None


@pytest.mark.asyncio
async def test_replan_budget_stops_same_fingerprint(monkeypatch):
    from clipwright.config import settings

    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)

    class _Task:
        pass

    class _Step:
        capability = "replan_closure"
        args = {"redo_agent": "animation", "fingerprint": "mg_motion_weak"}

    task, step = _Task(), _Step()
    first = await stage_closure.run(task, step)
    second = await stage_closure.run(task, step)
    third = await stage_closure.run(task, step)
    assert first["output"]["stages"] == ["animation", "audio"]
    assert second["output"]["stages"] == ["animation", "audio"]
    assert third["output"]["exhausted"] is True
    assert third["output"]["stages"] == []


@pytest.mark.asyncio
async def test_critique_call_passes_max_tokens(monkeypatch):
    """critique 必须和生成/修复一样下发 max_tokens，否则长 issues 会被截断。"""
    from clipwright.services.llm import MAX_OUTPUT_TOKENS

    gen = object.__new__(MGGenerator)
    gen._config = {
        "llm": {"max_tokens": 0, "timeout": 30},
        "critique": {"enabled": True},
        "prompt": {"system_template": ""},
    }
    captured: dict = {}

    class _FakeLLM:
        async def generate(self, **kwargs):
            captured.update(kwargs)

            class _Resp:
                content = '{"score": 80, "issues": [], "suggestions": []}'
                raw_response = None

            return _Resp()

    gen._llm = _FakeLLM()
    monkeypatch.setattr(MGGenerator, "_knowledge_section", lambda self, *a, **k: "")
    parsed = await gen._critique_quality(
        {"duration_sec": 3, "elements": []},
        "描述", {}, {}, "",
        scene_context={"title": "t"},
    )
    assert parsed and parsed["score"] == 80
    assert captured.get("max_tokens") == MAX_OUTPUT_TOKENS


def test_easing_family_alias_normalized():
    """ease-out-cubic / easeInOutQuad 等常见写法必须归一，否则整段文字动画回退模板。"""
    from clipwright.animation.mg.validator import normalize_easing

    assert normalize_easing("ease-out-cubic") == "ease-out"
    assert normalize_easing("easeInOutQuad") == "ease-in-out"
    assert normalize_easing("ease-in-sine") == "ease-in"


def test_shape_missing_field_repaired_to_rect():
    mg = {
        "duration_sec": 3,
        "elements": [
            {"type": "shape", "x": 100, "y": 100, "width": 20, "height": 20,
             "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]},
        ],
    }
    repaired, fixes = repair_mg_json(mg)
    assert repaired["elements"][0]["shape"] == "rect"
    assert any("missing shape" in f for f in fixes)


def test_persist_timeline_tick_writes_project(monkeypatch):
    """每完成一个动画都要写回项目时间线（动画轴增量可见）。"""
    from clipwright.services import project_manager

    calls: list[tuple[str, dict]] = []

    class _PM:
        def save(self, pid, data):
            calls.append((pid, data))
            return data

    monkeypatch.setattr(project_manager, "ProjectManager", _PM)
    agent = object.__new__(AnimationAgent)
    agent._project_id = "proj_test"
    agent._persist_timeline_tick({"tracks": [], "duration_sec": 0})
    assert calls and calls[0][0] == "proj_test"
    assert "timeline" in calls[0][1]


def test_text_animation_accepts_drawtext_easing():
    """drawtext 提示词的 ease-out-cubic 不能被 MG 白名单否决（否则必回退模板）。"""
    from clipwright.animation.text_generation import _validate_text_easing_neutral

    mg = {
        "animation_id": "text_test", "duration_sec": 3,
        "width": 1920, "height": 1080,
        "text_animation": {
            "style": {"font_size": 56, "fill": "#ffffff", "stroke_width": 2},
            "motion": {"type": "fade_in", "reveal_sec": 0.5},
            "display_sec": 2.0,
            "duration_reason": "测试：短关键词",
        },
        "elements": [{
            "type": "text", "content": "关键词", "x": "center", "y": "center",
            "keyframes": [
                {"time": 0, "opacity": 0, "easing": "ease-out-cubic"},
                {"time": 1, "opacity": 1, "easing": "ease-out-cubic"},
            ],
        }],
    }
    assert _validate_text_easing_neutral(mg, duration_s=3) == []


@pytest.mark.asyncio
async def test_critique_retries_invalid_json_once(monkeypatch):
    gen = object.__new__(MGGenerator)
    gen._config = {
        "llm": {"max_tokens": 0, "timeout": 30},
        "critique": {"enabled": True},
        "prompt": {"system_template": ""},
    }
    calls = {"n": 0}

    class _FakeLLM:
        async def generate(self, **kwargs):
            calls["n"] += 1
            text = "这不是 JSON，只是解释" if calls["n"] == 1 else \
                '{"score": 75, "issues": [], "suggestions": ["加个缓动"]}'

            class _Resp:
                content = text
                raw_response = None

            return _Resp()

    gen._llm = _FakeLLM()
    monkeypatch.setattr(MGGenerator, "_knowledge_section", lambda self, *a, **k: "")
    parsed = await gen._critique_quality(
        {"duration_sec": 3, "elements": []}, "描述", {}, {}, "",
        scene_context={"title": "t"},
    )
    assert parsed and parsed["score"] == 75
    assert calls["n"] == 2


def test_blackboard_reset_timeline_allows_upstream_redo():
    from clipwright.services import task_blackboard as bb

    tid = "t_reset_timeline_test"
    bb.reset(tid)
    tl_edit = {"tracks": [{"kind": "video", "clips": []}]}
    tl_audio = {"tracks": [{"kind": "video", "clips": []}, {"kind": "audio", "clips": []}]}
    bb.put(tid, "timeline", tl_edit, by="edit")
    bb.put(tid, "timeline", tl_audio, by="audio")
    assert bb.owner_of(tid, "timeline") == "audio"
    # 正常 put 必须拒绝倒序写
    try:
        bb.put(tid, "timeline", tl_edit, by="animation")
        assert False, "倒序写应被拒绝"
    except bb.BlackboardError:
        pass
    # 受控 reset 才允许回退
    bb.reset_timeline(tid, tl_edit, by="edit", reason="redo animation")
    assert bb.owner_of(tid, "timeline") == "edit"
    assert bb.get(tid)["timeline"] == tl_edit
