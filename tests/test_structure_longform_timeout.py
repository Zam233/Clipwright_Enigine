"""长文稿的墙钟与工具入参（E2E 2026-10-28 计划生成失败的根因）。

现象（702s 配音 / 4203 字文稿，fresh 会话点「确认」）：
- 需求侧 structrue 跑到 300s 整被掐断 → `TimeoutError`（str 为空）；
- 前端只看到「规划书生成失败: 」（连超时都看不出来），会话退回 brief_ready；
- 同一轮日志里还有 `list_animations 执行异常: '>' not supported between
  instances of 'str' and 'int'`（LLM 把 top_k 传成字符串）。

修复后的契约：
1. `run_inline` 默认墙钟 ≥ 600s，需求侧规划书路径显式给 900s（必须比
   StructureAgent 自身的 LLM 超时更宽松，否则内层降级路径永远走不到）；
2. StructureAgent.timeout_sec 覆盖为 600s（最重的 LLM 调用）；
3. `list_animations` 对 category/top_k 做类型收敛，字符串入参不再炸。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.agents.structure_agent import StructureAgent
from clipwright.services import requirements_service as rs
from clipwright.services import stage_subagent
from clipwright.tool.animation_list import ListAnimationsTool


def test_stage_local_timeout_covers_structure_llm_timeout():
    assert stage_subagent.DEFAULT_STAGE_TIMEOUT_LOCAL >= 600
    assert StructureAgent.timeout_sec >= 600
    assert rs.PLAN_STRUCTURE_TIMEOUT > StructureAgent.timeout_sec, (
        "外层墙钟必须比内层 LLM 超时宽，内层才能走「超时→兜底分镜」而不是被整段杀掉"
    )


@pytest.mark.asyncio
async def test_list_animations_accepts_string_top_k():
    out = await ListAnimationsTool().execute(category="all", top_k="0")

    assert out.status.value == "success" if hasattr(out.status, "value") else True
    assert out.output["total_available"] > 0


@pytest.mark.asyncio
async def test_list_animations_string_category_is_normalised():
    out = await ListAnimationsTool().execute(category=" TEXT ", top_k="3")

    cats = out.output["categories"]
    # 插件未加载时列表可能为空，这里只验"按 text 过滤且没抛异常"
    assert set(cats) == {"text_animations"}
    assert isinstance(cats["text_animations"], list)


@pytest.mark.asyncio
async def test_list_animations_bad_top_k_falls_back_to_default():
    out = await ListAnimationsTool().execute(category="all", top_k="oops")

    assert out.output["total_available"] > 0


def test_plan_generate_wraps_timeout_with_type_name(monkeypatch):
    """_generate_plan 的异常日志必须带类型名（TimeoutError 的 str 是空串）。"""
    captured: list[tuple] = []

    class _Svc(rs.RequirementsService):
        pass

    svc = _Svc.__new__(_Svc)

    async def _boom(*a, **k):  # noqa: ANN002, ANN003, ANN201
        raise asyncio.TimeoutError()

    monkeypatch.setattr(stage_subagent, "run_inline", _boom)

    def _fake_exception(fmt, *args):  # noqa: ANN001, ANN002, ANN003
        captured.append((fmt, args))

    monkeypatch.setattr(rs.logger, "exception", _fake_exception)
    monkeypatch.setattr(rs, "load_persona_by_id", lambda *a, **k: None, raising=False)

    out = asyncio.run(svc._generate_plan({"title": "t"}, {"topic": "t"}, "s1"))

    assert out is None
    assert captured, "必须打日志"
    fmt, args = captured[0]
    assert "TimeoutError" in args, f"日志里要出现异常类型名，实际 {args}"
