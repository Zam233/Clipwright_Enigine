"""C0：能力矩阵（路线 C 的第一个硬门槛）。

这三组断言对应 PRD 的 A4（权限档生效）与 A7（能力覆盖）的**前置条件**：
矩阵本身必须完整、可信、可复现，否则后面的 90% 覆盖面就是自欺欺人。
"""

from __future__ import annotations

import pytest

from clipwright.services import capability_matrix as cm


def test_registry_coverage_is_exhaustive():
    """注册表里每个工具都必须有明确档位；漏一个就抛（新增能力不能悄悄出现）。"""
    matrix = cm.build_matrix()
    tools = [e for e in matrix if e.kind == "tool"]
    assert tools, "工具矩阵不能为空（注册表没初始化？）"
    assert all(e.tier in cm.TIERS for e in tools)
    # 数量与注册表一致（说明没有未裁档位的工具被静默跳过）
    from clipwright.tool.registry import ToolRegistry

    assert len(tools) == len(ToolRegistry.list())


def test_stale_tier_entries_are_rejected():
    """TIER_MAP 里留了已删除的工具 → 也要报错（防止档位表腐烂）。"""
    original = dict(cm.TIER_MAP)
    try:
        cm.TIER_MAP["__ghost_tool__"] = "read"
        with pytest.raises(cm.CapabilityMatrixError, match="已不存在"):
            cm.build_matrix()
    finally:
        cm.TIER_MAP.clear()
        cm.TIER_MAP.update(original)


def test_unknown_tool_is_rejected(monkeypatch):
    """新增但未裁档位的工具 → 必须抛错，而不是默认给个档。"""
    from clipwright.tool.registry import ToolRegistry

    class _FakeInfo:
        name = "__not_registered_anywhere__"
        available = True
        description = "fake"
        parameters = {}

    real = ToolRegistry.list()

    monkeypatch.setattr(
        ToolRegistry, "list", classmethod(lambda cls: [*real, _FakeInfo()])
    )
    with pytest.raises(cm.CapabilityMatrixError, match="裁权限档"):
        cm.build_matrix()


def test_dangerous_tier_list_is_explicit():
    """dangerous 档必须点名，且 shell/python 只能落在 dangerous。"""
    stat = cm.coverage()
    assert "video_download" in stat["dangerous"], "下载外部资源必须按 dangerous 处理"
    matrix = {e.name: e for e in cm.build_matrix()}
    for name in ("run_shell", "run_python"):
        assert matrix[name].tier == "dangerous", f"{name} 必须是 dangerous"
    # 只有危险的几项，不允许大面积泛化（否则档位失去意义）
    assert len(stat["dangerous"]) <= 6


def test_read_tier_is_idempotent_and_writes_are_not():
    matrix = cm.build_matrix()
    for e in matrix:
        if e.tier == "read":
            assert e.idempotent is True, f"{e.name} 是只读能力，应可重放"
        if e.tier in ("write_local", "dangerous"):
            assert e.idempotent is False, f"{e.name} 会写/执行，默认不可重放"


def test_meta_capabilities_present():
    names = {e.name for e in cm.build_matrix() if e.kind in ("pipeline", "subagent", "meta")}
    assert {"run_pipeline", "spawn_subagent", "run_shell", "run_python"} <= names
    stat = cm.coverage()
    assert stat["by_kind"]["pipeline"] == 1
    assert stat["by_kind"]["skill"] >= 4


def test_coverage_report_shape_and_current_reality():
    """把「现状」钉成数字：C0 的 LLM 可见比例很低，这是事实，不许悄悄改口径。"""
    stat = cm.coverage()
    assert stat["total"] >= 55
    assert 0.0 <= stat["agent_callable_ratio"] <= 1.0
    # C0 基线：写成断言是为了让 C1 的进展可见（A7 目标 ≥90%）
    assert stat["agent_callable"] < stat["total"] // 2, (
        "若这里失败，说明 LLM 可见能力已大幅提升：请同步更新 PRD/C0 基线与 A7 判据"
    )


def test_render_markdown_is_stable_and_complete():
    md = cm.render_markdown()
    assert md.startswith("# 能力矩阵")
    assert "`video_trim`" in md
    assert "| 能力 | 类型 | 权限档 |" in md
    # 汇总里的 dangerous 点名
    assert "video_download" in md


def test_matrix_order_is_deterministic():
    first = [(e.kind, e.tier, e.name) for e in cm.build_matrix()]
    second = [(e.kind, e.tier, e.name) for e in cm.build_matrix()]
    assert first == second
