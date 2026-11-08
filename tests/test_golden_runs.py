"""C0：golden runs 的选题定义与指标比对。

两条断言最重要：

- 选题必须是 **10 个真实可读的题目**，覆盖 4 个内置类型插件与多种时长（不是凑数）；
- 比对必须**单向惩罚**：变贵变慢要红、明显缩水要红，变好不许红。
"""

from __future__ import annotations

import json

import pytest

from clipwright.services import golden_runs as gr

BUILTIN_PLUGINS = {"knowledge_long", "meme_fast", "gadget_review", "vlog_daily"}


def test_topics_load_and_cover_builtin_plugins():
    topics = gr.load_topics()
    assert len(topics) == 10
    assert {t.category_plugin_id for t in topics} == BUILTIN_PLUGINS
    assert all(t.topic.strip() for t in topics)
    assert all(t.duration_sec > 0 for t in topics)


def test_topics_cover_short_mid_and_long():
    durations = sorted(t.duration_sec for t in gr.load_topics())
    assert durations[0] <= 20, "要有极短（≤20s）选题"
    assert any(60 <= d <= 120 for d in durations), "要有 1–2 分钟档"
    assert durations[-1] >= 300, "要有 ≥5 分钟长片（验证上下文压缩）"


def test_topics_include_no_material_scenario():
    """US2 的能力必须有固定考题：素材缺失时能不能自己改计划。"""
    ids = {t.id for t in gr.load_topics()}
    assert "g09_no_material" in ids


def test_load_topics_validates_shape(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"topics": [{"id": "x"}]}), encoding="utf-8")
    with pytest.raises(gr.GoldenError, match="缺少字段"):
        gr.load_topics(p)


def test_duplicate_topic_ids_rejected(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps([
        {"id": "a", "topic": "t", "duration_sec": 10},
        {"id": "a", "topic": "t2", "duration_sec": 20},
    ]), encoding="utf-8")
    with pytest.raises(gr.GoldenError, match="重复"):
        gr.load_topics(p)


def test_metrics_are_noise_free():
    """指标里不能出现 id/时间戳这类噪声字段，否则 diff 永远在报变化。"""
    timeline = {
        "pipeline_id": "pl_noisy",
        "created_at": "2026-10-17T00:00:00Z",
        "duration_sec": 60,
        "tracks": [
            {"id": "tr_1", "kind": "video", "clips": [
                {"id": "c1", "kind": "video", "start_sec": 0, "duration_sec": 30,
                 "transition": "fade"},
                {"id": "c2", "kind": "image", "start_sec": 30, "duration_sec": 30},
            ]},
            {"id": "tr_2", "kind": "subtitle", "clips": [
                {"id": "s1", "kind": "subtitle", "start_sec": 0, "duration_sec": 60},
            ]},
            {"id": "tr_3", "kind": "audio", "clips": [
                {"id": "a1", "kind": "audio", "start_sec": 0, "duration_sec": 60},
            ]},
        ],
    }
    metrics = gr.metrics_of_timeline(timeline)
    assert "pipeline_id" not in metrics and "created_at" not in metrics
    assert metrics["tracks"] == 3
    assert metrics["clips"] == 4
    assert metrics["subtitles"] == 1
    assert metrics["subtitle_coverage"] == 1.0
    assert metrics["transitions"] == 1
    assert metrics["video_clips"] == 2


def test_metrics_accept_pydantic_like_object():
    class _TL:
        def model_dump(self, mode: str = "python"):  # noqa: ANN001, ARG002
            return {"duration_sec": 10, "tracks": [{"kind": "video", "clips": []}]}

    metrics = gr.metrics_of_timeline(_TL())
    assert metrics["duration_sec"] == 10
    assert metrics["clips"] == 0


def test_compare_passes_on_equal_metrics():
    base = {"duration_sec": 60, "clips": 10, "cost_usd": 1.0}
    result = gr.compare(base, dict(base))
    assert result.passed and not result.failures


def test_compare_fails_when_quality_drops_beyond_tolerance():
    base = {"clips": 10}
    result = gr.compare(base, {"clips": 8})  # -20% > 10%
    assert not result.passed
    assert "明显下降" in result.failures[0]


def test_compare_fails_when_cost_grows_beyond_tolerance():
    base = {"cost_usd": 1.0}
    result = gr.compare(base, {"cost_usd": 1.5})  # +50% > 30%
    assert not result.passed
    assert "超出上限" in result.failures[0]


def test_compare_does_not_punish_improvements():
    """比基线更好（更多片段、更便宜）不许红。"""
    base = {"clips": 10, "cost_usd": 1.0, "wall_sec": 100}
    result = gr.compare(base, {"clips": 14, "cost_usd": 0.6, "wall_sec": 80})
    assert result.passed


def test_assert_golden_pass_message_is_actionable():
    baseline = gr.GoldenBaseline(topic_id="g01", metrics={"clips": 10})
    with pytest.raises(gr.GoldenError, match="g01"):
        gr.assert_golden_pass(baseline, {"clips": 1})


def test_baselines_load_and_missing_report(tmp_path):
    p = tmp_path / "baselines.json"
    p.write_text(json.dumps({"baselines": [
        {"topic_id": "g01_knowledge_long", "metrics": {"clips": 3}, "recording": "r1.jsonl"},
    ]}), encoding="utf-8")
    baselines = gr.load_baselines(p)
    assert baselines["g01_knowledge_long"].metrics == {"clips": 3.0}
    missing = gr.topics_missing_baseline(baselines=baselines)
    assert "g01_knowledge_long" not in missing
    # 其余 9 个选题还没有基线（C0 期间允许非空，但必须可见）
    assert len(missing) == len(gr.load_topics()) - 1 == 9


def test_stale_baselines_are_flagged(tmp_path):
    """基线里留了已删除的选题 id → 必须能报出来（否则"基线通过"是假象）。"""
    p = tmp_path / "baselines.json"
    p.write_text(json.dumps({"baselines": [
        {"topic_id": "g99_removed", "metrics": {"clips": 3}},
    ]}), encoding="utf-8")
    baselines = gr.load_baselines(p)
    stale = gr.stale_baselines(baselines=baselines)
    assert stale == ["g99_removed"]
