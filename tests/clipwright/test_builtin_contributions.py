"""M3 测试：内置贡献镜像入账 + 冲突基准 + 转调后置校验（A-5 验收）。"""
from __future__ import annotations

import pytest

from clipwright.plugins.builtin_contributions import ingest_builtin_contributions
from clipwright.plugins.contribution import (
    BUILTIN_OWNER,
    Contribution,
    ContributionKind,
    ContributionRegistry,
)


@pytest.fixture(autouse=True)
def _clean():
    ContributionRegistry.clear_all()
    yield
    ContributionRegistry.clear_all()


class TestBuiltinIngest:
    def test_registry_counts_match(self):
        """A-5 验收：入账条目数与各注册表实际条目数一致（防止迁移期漏注册）。"""
        counts = ingest_builtin_contributions()

        from clipwright.tool.registry import ToolRegistry
        from clipwright.skill.registry import SkillRegistry
        from clipwright.agents.registry import AgentRegistry
        from clipwright.category.registry import CategoryRegistry
        from clipwright.material.registry import MaterialRegistry

        # 注意：测试环境未跑 main.py 启动，各表可能为空——断言「一致性」而非绝对值
        builtin_tools = [n for n, t in ToolRegistry._tools.items()
                         if not getattr(t, "_plugin_id", "")]
        assert counts["tool"] == len(builtin_tools)

        builtin_skills = [n for n, s in SkillRegistry._skills.items()
                          if not str(getattr(s, "_plugin_id", "") or "")]
        assert counts["skill"] == len(builtin_skills)

        builtin_agents = [n for n, e in AgentRegistry._agents.items()
                          if not str(getattr(e, "plugin_id", "") or "")]
        assert counts["agent"] == len(builtin_agents)

        owner_map = dict(getattr(CategoryRegistry, "_owner_plugin", {}) or {})
        builtin_cats = [c for c in CategoryRegistry._plugins if not owner_map.get(c)]
        assert counts["category"] == len(builtin_cats)

        builtin_sources = [s for s, src in MaterialRegistry._sources.items()
                           if not str(getattr(src, "_plugin_id", "") or "")]
        assert counts["material_source"] == len(builtin_sources)

    def test_ingest_with_seeded_builtins(self):
        """种一个内置工具 → 入账后 owner 为 BUILTIN_OWNER 且可被管理端点列出。"""
        from clipwright.tool.registry import ToolRegistry

        class SeedTool:
            name = "audit_m3_builtin_tool"
            description = "种子内置"
            required_tools = []

            async def run(self):
                return None

        ToolRegistry.register(SeedTool(), plugin_id="")
        try:
            counts = ingest_builtin_contributions()
            assert counts["tool"] >= 1
            assert ContributionRegistry.owner_of(
                ContributionKind.TOOL, "audit_m3_builtin_tool") == BUILTIN_OWNER

            # 冲突基准：插件注册同名 → 门面拒绝
            report = ContributionRegistry.register("evil_plug", [
                Contribution(ContributionKind.TOOL, "audit_m3_builtin_tool", SeedTool()),
            ])
            assert not report.ok
            assert "内置" in report.rejected[0][1]
        finally:
            ToolRegistry.unregister("audit_m3_builtin_tool")

    def test_ingest_idempotent(self):
        from clipwright.tool.registry import ToolRegistry

        class SeedTool2:
            name = "audit_m3_builtin_tool2"
            description = "种子内置2"
            required_tools = []

            async def run(self):
                return None

        ToolRegistry.register(SeedTool2(), plugin_id="")
        try:
            c1 = ingest_builtin_contributions()
            c2 = ingest_builtin_contributions()
            assert c1 == c2  # 幂等：重复入账计数不变
            owners = ContributionRegistry.list(ContributionKind.TOOL)
            assert all(o == BUILTIN_OWNER for o, _ in owners)
        finally:
            ToolRegistry.unregister("audit_m3_builtin_tool2")


class TestDispatchPostVerify:
    def test_silent_builtin_refusal_is_reported(self):
        """ToolRegistry 静默拒绝内置覆盖时，dispatch 后置校验必须报错。

        （防「门面误记归属 → 卸载时误删内置」——这是 Gate G-1 的兜底。）
        """
        from clipwright.tool.registry import ToolRegistry

        class RealBuiltin:
            name = "audit_m3_verify_tool"
            description = "内置"
            required_tools = []

            async def run(self):
                return None

        class PluginCopy:
            name = "audit_m3_verify_tool"
            description = "插件同名"
            required_tools = []

            async def run(self):
                return None

        ToolRegistry.register(RealBuiltin(), plugin_id="")
        try:
            report = ContributionRegistry.register("verify_plug", [
                Contribution(ContributionKind.TOOL, "audit_m3_verify_tool", PluginCopy()),
            ])
            assert not report.ok
            assert "未被注册表接受" in report.rejected[0][1]
            # 内置仍在，且门面没有误记插件归属
            assert ToolRegistry.get("audit_m3_verify_tool").__class__ is RealBuiltin
            assert ContributionRegistry.owner_of(
                ContributionKind.TOOL, "audit_m3_verify_tool") == ""
        finally:
            ToolRegistry.unregister("audit_m3_verify_tool")
