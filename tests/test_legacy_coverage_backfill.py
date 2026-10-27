"""B7：拆除带走的覆盖回补（closeout plan B7 / C10·C11）。

删老编排器时两份用例一起没了，这里把**仍然成立的不变量**钉回来：

1. **`stage_inputs` 的音频/人格/质量字段映射**（原 `test_pipeline_v2_audio_config`
   与 `test_pipeline_persona_flow` 的等价断言）：`assemble()` 必须把 board/override
   里的字段原样搬进对应 `XInput`，缺必需键要抛 `StageInputMissing`。
2. **Mongo 持久化文档形状**（原 `test_pipeline_v2_persist_shape`）：`PipelineModel`
   的 `steps` / `request` / `status` 形状是"重启恢复 + 历史兼容"的契约，字段名不许漂。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.schema.agent import AgentContext
from clipwright.services import stage_inputs


def _ctx(**extra) -> AgentContext:
    return AgentContext(
        pipeline_id="t_in", persona_id="p", category_plugin_id="knowledge_longform",
        topic="测试选题", extra_params=dict(extra),
    )


# ── 1. stage_inputs 字段映射 ─────────────────────────────


def test_audio_input_maps_config_and_timeline():
    ctx = _ctx()
    timeline = {"duration_sec": 12.0, "tracks": []}
    out = stage_inputs.assemble(
        "audio", task=SimpleNamespace(id="t"), context=ctx,
        board={"timeline": timeline},
        override={"audio_config": {"voice_id": "v1", "subtitle": True,
                                   "bgm": "b1", "dub_segments": [{"i": 1}]}},
    )
    assert out.audio_config["voice_id"] == "v1"
    assert out.audio_config["subtitle"] is True
    assert out.audio_config["dub_segments"] == [{"i": 1}]
    assert out.timeline.duration_sec == 12.0
    assert out.creative_brief == ctx.extra_params.get("creative_brief")


def test_audio_input_requires_timeline():
    with pytest.raises(stage_inputs.StageInputMissing) as exc:
        stage_inputs.assemble("audio", task=SimpleNamespace(id="t"),
                             context=_ctx(), board={}, override={})
    assert "timeline" in exc.value.missing


def test_quality_input_maps_constraints():
    out = stage_inputs.assemble(
        "quality", task=SimpleNamespace(id="t"), context=_ctx(),
        board={"timeline": {"duration_sec": 5.0, "tracks": []}},
        override={"constraints": {"max_shot_sec": 3.0, "min_shot_sec": 0.5}},
    )
    assert out.constraints["max_shot_sec"] == 3.0
    assert out.constraints["min_shot_sec"] == 0.5


def test_structure_input_takes_persona_fields():
    """人格字段（persona_config / prompt / vision / rag）必须原样进 StructureInput。"""
    out = stage_inputs.assemble(
        "structure", task=SimpleNamespace(id="t"), context=_ctx(),
        board={},
        override={
            "persona_config": {"tone": "克制"},
            "persona_prompt": "你是知识区作者",
            "vision_prompt": "看图说话",
            "rag_context": "背景资料",
            "creative_brief": {"text": "brief 文本"},
            "production_plan": {"scene_count": 6},
        },
    )
    assert out.persona_config["tone"] == "克制"
    assert out.persona_prompt == "你是知识区作者"
    assert out.vision_prompt == "看图说话"
    assert out.rag_context == "背景资料"
    assert out.creative_brief["text"] == "brief 文本"
    assert out.production_plan["scene_count"] == 6


def test_override_beats_board():
    """override（主 agent 显式给的）优先于黑板旧值——这是 S2 的硬要求。"""
    out = stage_inputs.assemble(
        "edit", task=SimpleNamespace(id="t"), context=_ctx(),
        board={"script_skeleton": {"scenes": [{"n": 1}]}, "candidate_clips": [{"id": "old"}]},
        override={"candidate_clips": [{"id": "new"}]},
    )
    assert out.candidate_clips[0]["id"] == "new"


def test_unknown_stage_raises():
    with pytest.raises(ValueError, match="未知阶段|未知"):
        stage_inputs.assemble("nope", task=SimpleNamespace(id="t"),
                              context=_ctx(), board={}, override={})


# ── 2. Mongo 持久化文档形状 ─────────────────────────────


def test_pipeline_model_document_shape_stable():
    """`PipelineModel` 落库后的字段名是重启恢复与历史兼容的契约，不许悄悄改名。

    它是 Mongo 文档类（`**kwargs` 构造 + `to_dict()`），不是 pydantic 模型，
    所以断言的是 **落库形状** 而不是 `model_fields`。
    """
    from clipwright.models.pipeline_model import PipelineModel

    doc = PipelineModel(
        id="pl_shape",
        request={"topic": "t", "persona_id": "p", "category_plugin_id": "c"},
        status="running",
        steps=[{"agent_name": "structure", "status": "completed",
                "started_at": "2026-10-27T00:00:00+00:00", "duration_ms": 12,
                "result": {"scenes": []}, "retry_count": 0}],
        shared_data={"scene_count": 1},
    )
    payload = doc.to_dict()
    for key in ("request", "status", "steps", "shared_data"):
        assert key in payload, f"落库文档缺字段 {key}"
    step = payload["steps"][0]
    for key in ("agent_name", "status", "started_at", "duration_ms", "retry_count"):
        assert key in step, f"步骤文档缺字段 {key}（历史 run 记录会读不到）"
    assert payload["request"]["topic"] == "t"
    assert payload["status"] == "running"


def test_run_history_maps_mongo_doc_shape():
    """`get_run_records` 把 Mongo 文档映射成前端 run 形状的那段也要保住。"""
    from datetime import datetime, timezone

    from clipwright.services import pipeline_history

    doc = SimpleNamespace(
        id="pl_hist",
        request={"topic": "历史选题"},
        status="completed",
        created_time=datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc),
        updated_time=datetime(2026, 10, 27, 10, 0, 5, tzinfo=timezone.utc),
        steps=[{"agent_name": "edit", "status": "completed",
                "started_at": "2026-10-27T10:00:01+00:00", "duration_ms": 1000,
                "retry_count": 0}],
    )
    rec = pipeline_history._mongo_record_to_run(doc)  # noqa: SLF001
    assert rec is not None
    assert rec["id"] == "pl_hist"
    assert rec["topic"] == "历史选题"
    assert rec["duration_ms"] == 5000
    assert rec["agents"][0]["agent"] == "edit"
    assert rec["agents"][0]["status"] == "ok"
