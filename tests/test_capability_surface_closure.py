"""能力面收口：skill 进工具面 / 子代理超时真生效 / 取消真能停。

这三个都是"看起来有了其实没有"的类型，所以逐条钉死：
1. 4 个内置技能此前**不在 Kernel 工具面**（surface 只遍历 tool），主代理一个技能都调不了；
2. `spec.timeout_sec` 只写进规格单、没有任何实际约束，挂住的模型调用会把一步永久卡住；
3. `cancel()` 只改状态文字，底层协程继续跑（tracker 的 cancel_cb 从未绑定）。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.config import settings
from clipwright.skill import register_builtin_skills
from clipwright.skill.registry import SkillRegistry
from clipwright.tool import register_builtin_tools
from clipwright.tool import surface as sf
from clipwright.tool.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _registered():
    register_builtin_tools()
    register_builtin_skills()
    sf.clear_records()
    sf.clear_cache()
    yield
    sf.clear_records()
    sf.clear_cache()


# ── 1. Skill 进工具面并可执行 ─────────────────────────────


def test_all_skills_are_in_surface():
    names = {e.name for e in sf.build_surface()}
    for info in SkillRegistry.list():
        assert info.name in names, f"技能 {info.name} 不在工具面里 —— 主 agent 调不了"


def test_skill_schemas_are_llm_ready():
    skill_names = {i.name for i in SkillRegistry.list()}
    schemas = [s for s in sf.schemas() if s["function"]["name"] in skill_names]
    assert len(schemas) == len(skill_names)
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"


def test_execute_dispatches_skill(monkeypatch):
    """surface.execute('技能名') 必须真的走 SkillRegistry，而不是报"不在工具面内"。"""
    calls: list[str] = []

    async def fake_execute(name, **kwargs):  # noqa: ANN001
        from clipwright.schema.skill import SkillExecResult

        calls.append(name)
        return SkillExecResult(skill_name=name, output={"ok": True},
                               tool_calls=[{"tool": "media_probe", "ok": True}])

    monkeypatch.setattr(SkillRegistry, "execute", staticmethod(fake_execute))
    skill = SkillRegistry.list()[0].name
    out = asyncio.run(sf.execute(skill, {}))
    assert calls == [skill]
    assert out["status"] == "ok"
    assert out["output"] == {"ok": True}
    # 技能内部的工具轨迹要透出来
    assert out["tool_calls"][0]["tool"] == "media_probe"
    assert out["record"]["name"] == skill


def test_skill_failure_is_structured(monkeypatch):
    async def boom(name, **kwargs):  # noqa: ANN001
        from clipwright.schema.skill import SkillExecResult, SkillStatus

        return SkillExecResult(status=SkillStatus.ERROR, skill_name=name, error="工具缺失")

    monkeypatch.setattr(SkillRegistry, "execute", staticmethod(boom))
    skill = SkillRegistry.list()[0].name
    out = asyncio.run(sf.execute(skill, {}))
    assert out["status"] == "error"
    assert out["error"] == "工具缺失"


def test_skill_absent_from_dangerous_tiers():
    """技能全是 write_local，不该被 dangerous 过滤误伤。"""
    entries = {e.name: e for e in sf.build_surface()}
    for info in SkillRegistry.list():
        assert entries[info.name].tier == "write_local"


def test_stats_count_skills_separately():
    stat = sf.surface_stats()
    assert stat["skills_in_surface"] == len(SkillRegistry.list())
    # 覆盖率仍然只按工具池算，不能把技能算进分子把比例做成 >100%
    assert 0.0 <= stat["coverage_ratio"] <= 1.0


# ── 2. 子代理超时真生效 ───────────────────────────────────


def test_worker_timeout_is_enforced(monkeypatch):
    from clipwright.services import llm as llm_mod
    from clipwright.supervisor import spawn as sp
    from clipwright.supervisor.tracker import clear_trackers
    from clipwright.supervisor.worker_registry import clear_workers

    clear_trackers()
    clear_workers()

    async def hang(self, prompt, **kwargs):  # noqa: ANN001
        await asyncio.sleep(5)
        return None

    monkeypatch.setattr(llm_mod.LLMService, "ask", hang)
    from types import SimpleNamespace

    result = asyncio.run(sp.spawn_subagent(
        SimpleNamespace(pipeline_id="task_to", extra_params={}),
        role="会挂住的角色", timeout_sec=0.05,
    ))
    assert result["status"] == "failed"
    assert result["failure"]["category"] == "timeout"
    assert result["failure"]["retryable"] == "retry_same"


def test_worker_cancel_really_stops(monkeypatch):
    """取消要 cancel 掉协程：结果里状态是 cancelled，而不是跑完变 done。"""
    from clipwright.services import llm as llm_mod
    from clipwright.supervisor import spawn as sp
    from clipwright.supervisor.tracker import clear_trackers, get_tracker
    from clipwright.supervisor.worker_registry import clear_workers

    clear_trackers()
    clear_workers()
    finished = {"ran": False}

    async def slow(self, prompt, **kwargs):  # noqa: ANN001
        await asyncio.sleep(5)
        finished["ran"] = True
        from types import SimpleNamespace

        return SimpleNamespace(success=True, content="跑完了", status_code=200)

    monkeypatch.setattr(llm_mod.LLMService, "ask", slow)

    async def scenario():  # noqa: ANN202
        from types import SimpleNamespace

        task = asyncio.create_task(sp.spawn_subagent(
            SimpleNamespace(pipeline_id="task_cancel", extra_params={}),
            role="会被取消的角色", timeout_sec=30,
        ))
        await asyncio.sleep(0.05)
        nodes = get_tracker("task_cancel").list_nodes()
        assert nodes, "应当已有节点"
        cancelled = get_tracker("task_cancel").cancel(nodes[0].id)
        assert cancelled is True
        return await task

    result = asyncio.run(scenario())
    assert result["status"] == "cancelled"
    assert result["failure"]["category"] == "cancelled"
    assert finished["ran"] is False, "取消后不应继续执行"


# ── 3. 工具面统计仍自洽 ───────────────────────────────────


def test_surface_includes_tool_and_skill_kinds():
    names = {e.name for e in sf.build_surface()}
    assert "media_probe" in names        # tool
    assert SkillRegistry.list()[0].name in names   # skill
    # dangerous 仍被排除
    assert "run_shell" not in names and "run_python" not in names
