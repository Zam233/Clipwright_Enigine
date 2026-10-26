"""类型插件解析与 run_pipeline 失败分类。

回归的两个真实故障（2026-10-23，agent_mode 切到 on 后首次跑）：
1. ``Unknown category plugin: ``（**空字符串**）——Kernel 的退化单步计划不带 args，
   请求里 category_plugin_id 为空，管线 `_init` 直接抛；
2. Kernel 把一切异常归为 ``unknown/unknown``，结论只剩"未成功：unknown"，没有原因。
"""

from __future__ import annotations

import pytest

from clipwright.category.registry import CategoryRegistry
from clipwright.schema.task import PlanStep, Task, TaskRequest
from clipwright.services import pipeline_capability as cap
from clipwright.services.pipeline_v2 import resolve_category_plugin


class _Cat:
    """最小类型插件（duck-typed：注册表只读 plugin_id / display_name / description）。"""

    def __init__(self, cid: str) -> None:
        self.plugin_id = cid
        self.display_name = cid
        self.description = ""

    def translate_persona(self, parameter):  # pragma: no cover - 本文件不调用
        return {}


@pytest.fixture
def two_categories():
    saved = dict(CategoryRegistry._plugins)
    CategoryRegistry.clear()
    CategoryRegistry.register(_Cat("a_type"))
    CategoryRegistry.register(_Cat("b_type"))
    try:
        yield
    finally:
        CategoryRegistry._plugins = saved


@pytest.fixture
def empty_categories():
    saved = dict(CategoryRegistry._plugins)
    CategoryRegistry.clear()
    try:
        yield
    finally:
        CategoryRegistry._plugins = saved


def test_empty_id_falls_back_to_default(two_categories) -> None:
    plugin, defaulted, error = resolve_category_plugin("")
    assert error == ""
    assert defaulted == "a_type"
    assert plugin is not None and plugin.plugin_id == "a_type"


def test_unknown_id_lists_available(two_categories) -> None:
    plugin, defaulted, error = resolve_category_plugin("nope")
    assert plugin is None and defaulted == ""
    assert "Unknown category plugin: nope" in error
    assert "a_type" in error and "b_type" in error


def test_no_categories_is_explicit(empty_categories) -> None:
    plugin, _defaulted, error = resolve_category_plugin("")
    assert plugin is None
    assert "CategoryRegistry 为空" in error


def test_known_id_passes_through(two_categories) -> None:
    plugin, defaulted, error = resolve_category_plugin("b_type")
    assert plugin is not None and plugin.plugin_id == "b_type"
    assert defaulted == "" and error == ""


def test_classify_unknown_category_is_invalid_input() -> None:
    failure = cap._classify_failure(ValueError("Unknown category plugin: nope"))
    assert failure["category"] == "invalid_input"
    assert failure["retryable"] == "needs_human"
    assert "nope" in failure["blocking_point"]


def test_classify_timeout_is_retryable() -> None:
    import asyncio

    failure = cap._classify_failure(asyncio.TimeoutError())
    assert failure["category"] == "timeout"
    assert failure["retryable"] == "retry_same"


@pytest.mark.asyncio
async def test_run_returns_structured_failure(monkeypatch) -> None:
    """管线抛错时 capability 必须给出 failure（Kernel 才能说清原因）。"""
    from clipwright.config import settings

    monkeypatch.setattr(cap, "enabled", lambda: True)
    # P2 起 pipeline_enabled 默认 false 会先于编排器拒绝；本用例要测的是异常归类
    monkeypatch.setattr(settings, "pipeline_enabled", True, raising=False)

    async def _allow():
        return True, 0

    monkeypatch.setattr("clipwright.services.budget.check_budget", _allow, raising=False)

    class _BoomOrch:
        async def run(self, request, **kwargs):
            raise ValueError("Unknown category plugin: ")

    monkeypatch.setattr(
        "clipwright.services.pipeline_v2.PipelineOrchestratorV2", _BoomOrch,
        raising=False,
    )
    task = Task(
        id="t_fail",
        request=TaskRequest(topic="测试", persona_id="default", category_plugin_id=""),
    )
    step = PlanStep(id="p1", capability="run_pipeline", kind="pipeline")
    result = await cap.run(task, step)
    assert result["status"] == "error"
    assert result["failure"]["category"] == "invalid_input"
    assert result["failure"]["retryable"] == "needs_human"
