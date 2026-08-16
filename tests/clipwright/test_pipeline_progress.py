"""C4/C5: 逐 Agent 时间线快照 + 细粒度进度事件测试。"""

from __future__ import annotations

from clipwright.services.pipeline import get_agent_progress, PipelineOrchestrator


def test_agent_progress_monotonic_100() -> None:
    """C5: 进度权重累计单调且终值为 100。"""
    order = ["structure", "material", "edit", "animation", "audio", "quality"]
    values = [get_agent_progress(a) for a in order]
    assert values == sorted(values)
    assert values[-1] == 100
    assert values[0] > 0
    # 未知 agent → 不低于前序（回退为 0 边界安全）
    assert get_agent_progress("unknown") == 0


def test_agent_progress_weights_sum() -> None:
    """C5: 权重表总和必须为 100（保证进度条封顶）。"""
    assert sum(PipelineOrchestrator.AGENT_PROGRESS_WEIGHTS.values()) == 100
