"""时间线编辑端点回归测试 — edit_timeline 三路分发（换素材/重做动画/数值调整）。

覆盖：
- adjust-ops 白名单与边界钳制（越界值钳到合法范围）
- 未选中 / 未知字段的 op 被忽略
- 子集合并保 id（redo_animation 只更新选中片段）
- 意图分类 LLM 不可用 → 默认 adjust，不抛异常
- replace_material 空候选 / 空建议素材 → 保留原片段 + 说明性回复（不 IndexError）
"""

from __future__ import annotations

import pytest

import clipwright.services.requirements_service as req_mod
from clipwright.schema.agent import AnimationOutput
from clipwright.schema.timeline import Timeline, Track
from clipwright.services.requirements_service import RequirementsService


def _mk_timeline() -> dict:
    """最小合法时间线（两个 video clip）。"""
    return {
        "id": "tl_1", "width": 1920, "height": 1080, "fps": 30.0, "duration_sec": 10.0,
        "tracks": [
            {
                "id": "tr_v", "name": "V1", "kind": "video", "index": 0,
                "clips": [
                    {
                        "id": "c1", "kind": "video", "asset_id": "C:\\library\\a.mp4",
                        "track_id": "tr_v", "start_sec": 0, "duration_sec": 5,
                        "source_offset_sec": 0, "speed": 1, "volume": 1, "opacity": 1,
                        "keyframes": [], "metadata": {},
                    },
                    {
                        "id": "c2", "kind": "video", "asset_id": "C:\\library\\b.mp4",
                        "track_id": "tr_v", "start_sec": 5, "duration_sec": 5,
                        "source_offset_sec": 0, "speed": 1, "volume": 1, "opacity": 1,
                        "keyframes": [], "metadata": {},
                    },
                ],
            }
        ],
    }


class FakeLLM:
    """可控 LLM：意图分类返回固定 action，adjust 解析返回固定 ops，可整体抛错。"""

    def __init__(self, intent: str = "adjust", ops: list | None = None, fail: bool = False):
        self.intent = intent
        self.ops = ops if ops is not None else []
        self.fail = fail
        self.calls: list[dict] = []

    async def structured_output(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("LLM down")
        schema = kwargs.get("output_schema") or {}
        if schema.get("properties", {}).get("action"):
            return {"action": self.intent}
        return {"ops": self.ops}


@pytest.fixture
def service():
    inst = RequirementsService()
    yield inst


@pytest.fixture(autouse=True)
def _clean_memory():
    yield
    req_mod._memory_sessions.clear()


def _seed_session(sid: str = "req_test") -> str:
    session = {
        "session_id": sid,
        "status": "plan_ready",
        "messages": [],
        "user_inputs": {
            "topic": "测试", "persona_id": "default",
            "category_plugin_id": "knowledge_longform",
        },
        "creative_brief": None,
        "production_plan": None,
    }
    req_mod._memory_sessions[sid] = session
    return sid


def _apply(service, timeline: dict, selected: list[str], llm: FakeLLM):
    service._llm = llm
    sid = _seed_session()
    return service.edit_timeline(sid, "把速度调快", timeline, selected)


# ── adjust：白名单与边界 ────────────────────────────

async def test_adjust_clamps_out_of_range_values(service):
    tl = _mk_timeline()
    llm = FakeLLM(intent="adjust", ops=[
        {"clip_id": "c1", "field": "speed", "value": 10},    # 钳到 4
        {"clip_id": "c1", "field": "volume", "value": 2},    # 钳到 1
        {"clip_id": "c1", "field": "opacity", "value": -1},  # 钳到 0
    ])
    res = await _apply(service, tl, ["c1"], llm)
    assert res["action"] == "adjust"
    proposed = Timeline.model_validate(res["proposed_timeline"])
    c1 = proposed.tracks[0].clips[0]
    assert c1.speed == 4.0
    assert c1.volume == 1.0
    assert c1.opacity == 0.0


async def test_adjust_ignores_unselected_clip_and_unknown_field(service):
    tl = _mk_timeline()
    llm = FakeLLM(intent="adjust", ops=[
        {"clip_id": "c2", "field": "speed", "value": 3},   # 选中集合外 → 忽略
        {"clip_id": "c1", "field": "bogus_field", "value": 5},  # 白名单外 → 忽略
    ])
    res = await _apply(service, tl, ["c1"], llm)
    proposed = Timeline.model_validate(res["proposed_timeline"])
    c1 = proposed.tracks[0].clips[0]
    c2 = proposed.tracks[0].clips[1]
    assert c1.speed == 1.0
    assert c2.speed == 1.0


async def test_adjust_no_ops_returns_original_timeline(service):
    tl = _mk_timeline()
    llm = FakeLLM(intent="adjust", ops=[])
    res = await _apply(service, tl, ["c1"], llm)
    proposed = Timeline.model_validate(res["proposed_timeline"])
    assert proposed.tracks[0].clips[0].speed == 1.0
    assert "0 项" in res["reply"]


# ── adjust：text/font_color 字符串字段 ───────────────

async def test_adjust_accepts_string_fields(service):
    tl = _mk_timeline()
    llm = FakeLLM(intent="adjust", ops=[
        {"clip_id": "c1", "field": "text", "value": "新标题"},
        {"clip_id": "c1", "field": "font_color", "value": "#FF0000"},
    ])
    res = await _apply(service, tl, ["c1"], llm)
    proposed = Timeline.model_validate(res["proposed_timeline"])
    c1 = proposed.tracks[0].clips[0]
    assert c1.text == "新标题"
    assert c1.font_color == "#FF0000"


# ── 意图分类 fallback ────────────────────────────────

async def test_intent_classification_fallback_when_llm_down(service):
    tl = _mk_timeline()
    llm = FakeLLM(intent="adjust", ops=[], fail=True)
    res = await _apply(service, tl, ["c1"], llm)
    # 意图分类失败 → 默认 adjust；adjust 解析也失败 → ops 空 → 原样返回 + 说明
    assert res["action"] == "adjust"
    proposed = Timeline.model_validate(res["proposed_timeline"])
    assert proposed.tracks[0].clips[0].speed == 1.0


async def test_no_selected_clips_is_noop(service):
    tl = _mk_timeline()
    llm = FakeLLM(intent="adjust", ops=[{"clip_id": "c1", "field": "speed", "value": 2}])
    res = await _apply(service, tl, [], llm)
    proposed = Timeline.model_validate(res["proposed_timeline"])
    assert proposed.tracks[0].clips[0].speed == 1.0


# ── replace_material：空值守卫 ────────────────────────

async def test_replace_material_empty_candidates_keeps_clip(service, monkeypatch):
    from clipwright.agents import material_agent

    class FakeMaterial:
        async def execute(self, input_data, context):
            return type("Out", (), {"candidate_clips": [{"suggested_assets": []}]})()

    monkeypatch.setattr(material_agent, "MaterialAgent", FakeMaterial)
    tl = _mk_timeline()
    llm = FakeLLM(intent="replace_material", ops=[])
    res = await _apply(service, tl, ["c1"], llm)
    assert "未找到替代素材" in res["reply"]
    proposed = Timeline.model_validate(res["proposed_timeline"])
    c1 = proposed.tracks[0].clips[0]
    assert c1.asset_id == "C:\\library\\a.mp4"  # 保持原样


async def test_replace_material_applies_suggested_asset(service, monkeypatch):
    from clipwright.agents import material_agent

    class FakeMaterial:
        async def execute(self, input_data, context):
            return type("Out", (), {"candidate_clips": [
                {"suggested_assets": [
                    {"asset_id": "new_video", "url": "http://cdn/new.mp4",
                     "local_path": "C:\\library\\new.mp4", "title": "新素材"},
                ]},
            ]})()

    monkeypatch.setattr(material_agent, "MaterialAgent", FakeMaterial)
    tl = _mk_timeline()
    llm = FakeLLM(intent="replace_material", ops=[])
    res = await _apply(service, tl, ["c1"], llm)
    assert "已更换素材" in res["reply"]
    proposed = Timeline.model_validate(res["proposed_timeline"])
    c1 = proposed.tracks[0].clips[0]
    assert c1.asset_id == "new_video"
    assert c1.metadata.get("url") == "http://cdn/new.mp4"
    assert c1.metadata.get("local_path") == "C:\\library\\new.mp4"


# ── redo_animation：子集合并保 id ─────────────────────

async def test_redo_animation_subset_merge_keeps_id(service, monkeypatch):
    from clipwright.agents import animation_agent

    def _result_timeline():
        tr = Track(
            id="tr_v", name="V1", kind="video", index=0,
            clips=[{
                "id": "c1", "kind": "video", "asset_id": "C:\\library\\a.mp4",
                "track_id": "tr_v", "start_sec": 0, "duration_sec": 5,
                "source_offset_sec": 0, "speed": 1, "volume": 1, "opacity": 1,
                "keyframes": [{"time": 0.0, "properties": {"opacity": 0}}],
                "metadata": {},
            }],
        )
        return Timeline(id="tl_1", width=1920, height=1080, fps=30.0, duration_sec=10.0, tracks=[tr])

    class FakeAnim:
        async def execute(self, input_data, context):
            return AnimationOutput(decision="pass", timeline=_result_timeline(), generated_mg_count=0)

    monkeypatch.setattr(animation_agent, "AnimationAgent", FakeAnim)
    tl = _mk_timeline()
    llm = FakeLLM(intent="redo_animation", ops=[])
    res = await _apply(service, tl, ["c1"], llm)
    proposed = Timeline.model_validate(res["proposed_timeline"])
    clips = {c.id: c for c in proposed.tracks[0].clips}
    # c1 被更新（带关键帧）；c2 未选中 → 保持原样
    assert len(clips["c1"].keyframes) == 1
    assert clips["c1"].keyframes[0]["properties"]["opacity"] == 0
    assert clips["c2"].keyframes == []
    assert "重做动画" in res["reply"]


async def test_edit_returns_proposed_timeline_json(service):
    tl = _mk_timeline()
    llm = FakeLLM(intent="adjust", ops=[{"clip_id": "c1", "field": "speed", "value": 1.5}])
    res = await _apply(service, tl, ["c1"], llm)
    assert "proposed_timeline" in res
    assert "reply" in res
    assert isinstance(res["proposed_timeline"], dict)
