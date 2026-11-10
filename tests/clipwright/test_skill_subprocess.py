"""M4：subprocess 插件的 Skill 声明 → 宿主适配 → RPC 执行。"""
from __future__ import annotations

import pytest

from clipwright.plugins.loader import PluginLoader
from clipwright.schema.skill import SkillStatus
from clipwright.skill.registry import SkillRegistry

PLUGIN_SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.schema.skill import SkillExecResult",
    "from clipwright.skill.base import BaseSkill",
    "",
    "",
    "class ProbeSkill(BaseSkill):",
    "    name = 'probe_skill'",
    "    description = 'probe'",
    "",
    "    async def execute(self, **kwargs):",
    "        return SkillExecResult(skill_name=self.name, output={'got': kwargs})",
    "",
    "",
    "class SkillPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(",
    "            ContributionKind.SKILL, 'probe_skill', ProbeSkill(),",
    "            meta={'name': 'probe_skill', 'description': 'probe',",
    "                  'llm_schema': {'name': 'probe_skill',",
    "                                 'description': 'probe',",
    "                                 'input_schema': {'type': 'object',",
    "                                                  'properties': {}}} },",
    "        )]",
    "",
])


@pytest.fixture
def loader(tmp_path):
    root = tmp_path / "plugins" / "skill_probe_pkg"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: skill_probe_pkg\nname: skill probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n  - kind: skill\n    id: probe_skill\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    instance = PluginLoader(plugin_dir=tmp_path / "plugins",
                            data_dir=tmp_path / "PluginData")
    yield instance
    instance.stop_all_hosts()
    SkillRegistry.clear()


@pytest.mark.asyncio
async def test_skill_adapter_executes_over_rpc(loader):
    loader.load("skill_probe_pkg")
    skill = SkillRegistry.get("probe_skill")
    assert skill is not None
    assert skill.name == "probe_skill"

    result = await skill.execute(x=1)
    assert result.status == SkillStatus.SUCCESS
    assert result.output == {"got": {"x": 1}}

    loader.unload("skill_probe_pkg")
    assert SkillRegistry.get("probe_skill") is None
