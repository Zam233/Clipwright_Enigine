"""归因与保留不变量（modern-agent PRD §2.4.2 / §4.4 / M3 收尾）。

本批删掉了老编排器，同时把两件事**搬**了出来（不是丢）：

1. **阶段 token 归因**：`StageResult.tokens` / `tool_calls` 有真实数字，
   上层 `AgentResult` / 评估 / 前端成本条才有东西可显示；
2. **最终时间线收尾**：类型插件 transform / `validate_clip` 规范校验 / 片尾授权名单，
   由 `services/timeline_finalize.py` 承担（§4.4 的领域不变量）。

另有熔断表（`services/circuit_breaker.py`）从编排器类属性迁成独立注册表。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.schema.stage import StageSpec
from clipwright.services import circuit_breaker, stage_subagent, timeline_finalize


class _FakeRequest:
    def __init__(self) -> None:
        self.topic = "测试选题"
        self.persona_id = "default"
        self.category_plugin_id = "knowledge_longform"
        self.extra: dict = {}


class _Task:
    def __init__(self, task_id: str = "task_tok") -> None:
        self.id = task_id
        self.request = _FakeRequest()
        self.context_digest: list = []


class _Output:
    def __init__(self, tool_calls: list[dict]) -> None:
        self.decision = "pass"
        self.degradations: list = []
        self.scenes = [{"n": 1}]
        self.tool_calls = tool_calls

    def model_dump(self, mode: str = "json") -> dict:
        return {"decision": "pass", "scenes": self.scenes}


def _patch_usage(monkeypatch, before: dict, after: dict) -> None:
    """让 `llm_usage_snapshot()` 依次返回 before/after（模拟该阶段真实消耗）。"""
    seq = iter([before, after])

    def fake_snapshot() -> dict:
        return dict(next(seq, after))

    import clipwright.services.llm as llm_mod

    monkeypatch.setattr(llm_mod, "llm_usage_snapshot", fake_snapshot, raising=False)


def test_stage_result_carries_real_tokens_and_tool_calls(monkeypatch):
    """阶段 run 的 token / 工具轨迹必须是真的，不是恒 0/空。"""
    calls = [{"tool": "frame_check", "input": {"path": "a.mp4"}}]

    class _Agent:
        last_tool_calls = calls

        async def execute(self, input_obj, context):  # noqa: ANN001, ARG002
            return _Output(calls)

    monkeypatch.setattr(stage_subagent, "agent_for", lambda stage: _Agent())
    _patch_usage(
        monkeypatch,
        {"input_tokens": 100, "output_tokens": 50, "calls": 1},
        {"input_tokens": 700, "output_tokens": 350, "calls": 3},
    )

    result = asyncio.run(stage_subagent.run_stage(
        _Task(), StageSpec(stage="quality", inputs={"timeline": {"tracks": []}})))

    assert result.tokens == 900, "token 取进程级差值（700+350 - 100-50）"
    assert result.tool_calls == calls, "工具轨迹要原样上交"


def test_stage_result_tokens_zero_when_no_llm(monkeypatch):
    """没花 token 的阶段就记 0（不是"未知"）——它有确定的测量口径。"""

    class _Agent:
        last_tool_calls: list = []

        async def execute(self, input_obj, context):  # noqa: ANN001, ARG002
            return _Output([])

    monkeypatch.setattr(stage_subagent, "agent_for", lambda stage: _Agent())
    _patch_usage(monkeypatch,
                 {"input_tokens": 10, "output_tokens": 10, "calls": 1},
                 {"input_tokens": 10, "output_tokens": 10, "calls": 1})

    result = asyncio.run(stage_subagent.run_stage(
        _Task("task_tok0"), StageSpec(stage="quality", inputs={"timeline": {"tracks": []}})))
    assert result.tokens == 0
    assert result.tool_calls == []


# ── 熔断表 ────────────────────────────────────────────


def test_breaker_opens_after_threshold_and_recovers(monkeypatch):
    circuit_breaker.reset()
    key = circuit_breaker.key_of("edit", "anthropic/a")
    for _ in range(circuit_breaker.THRESHOLD):
        circuit_breaker.record_failure(key)
    assert circuit_breaker.is_open(key) is True
    assert circuit_breaker.failed_models_for("edit") == ["anthropic/a"]

    circuit_breaker.record_success(key)
    assert circuit_breaker.is_open(key) is False
    assert circuit_breaker.failed_models_for("edit") == []
    circuit_breaker.reset()


def test_breaker_snapshot_shape():
    circuit_breaker.reset()
    circuit_breaker.record_failure("quality")
    snap = circuit_breaker.snapshot()
    assert snap["status"] == "ok"
    row = snap["agents"][0]
    for field in ("agent", "fail_count", "threshold", "open", "recovery_sec",
                  "seconds_since_fail"):
        assert field in row
    circuit_breaker.reset()


# ── 最终时间线收尾（§4.4 领域不变量）─────────────────


def _timeline_with_license() -> dict:
    return {
        "duration_sec": 10.0,
        "tracks": [
            {"id": "t1", "kind": "video", "clips": [
                {"id": "c1", "kind": "video", "asset_id": "a1", "track_id": "t1",
                 "start_sec": 0, "duration_sec": 10,
                 "metadata": {"license": "CC-BY", "author": "张三",
                              "source_title": "素材A"}},
            ]},
        ],
    }


def test_credit_outro_appended_with_source_list():
    tl = timeline_finalize.finalize(_timeline_with_license(), pid="t")
    caption = next(t for t in tl["tracks"] if t["kind"] == "caption")
    assert caption["clips"], "有授权元数据时必须追加片尾"
    text = caption["clips"][0]["text"]
    assert "素材来源" in text and "CC-BY" in text
    assert tl["duration_sec"] == pytest.approx(12.5)


def test_credit_outro_can_be_disabled():
    tl = timeline_finalize.finalize(_timeline_with_license(),
                                    extra_params={"credit_outro": False}, pid="t")
    assert not any(t["kind"] == "caption" for t in tl["tracks"])


def test_no_license_means_no_outro():
    tl = timeline_finalize.finalize({"duration_sec": 5.0, "tracks": [
        {"kind": "video", "clips": [{"id": "c", "kind": "video",
                                     "start_sec": 0, "duration_sec": 5}]}]}, pid="t")
    assert not any(t["kind"] == "caption" for t in tl["tracks"])


def test_transform_failure_degrades_but_keeps_timeline():
    class _Boom:
        plugin_id = "boom_type"

        def post_process_timeline(self, timeline):  # noqa: ANN001, ARG002
            raise RuntimeError("transform boom")

    recorded: list[tuple] = []
    tl = _timeline_with_license()
    out = timeline_finalize.finalize(
        tl, plugin=_Boom(), extra_params={"credit_outro": False}, pid="t",
        on_degradation=lambda *a: recorded.append(a),
    )
    assert out == tl, "后处理失败必须保留原时间线"
    assert recorded and recorded[0][0] == "plugin_transform_failed"


def test_validate_clip_violation_is_marked():
    class _Strict:
        plugin_id = "strict_type"

        def post_process_timeline(self, timeline):  # noqa: ANN001, ARG002
            return timeline

        def validate_clip(self, clip):  # noqa: ANN001, ARG002
            return False

    recorded: list[tuple] = []
    out = timeline_finalize.finalize(
        _timeline_with_license(), plugin=_Strict(),
        extra_params={"credit_outro": False}, pid="t",
        on_degradation=lambda *a: recorded.append(a),
    )
    clip = out["tracks"][0]["clips"][0]
    assert clip["metadata"]["type_violation"] is True
    assert any(r[0] == "type_violation" for r in recorded)
