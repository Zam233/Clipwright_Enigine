"""工具面必须容忍畸形/扁平 schema（E2E 2026-10-28 发现的第二个"整面炸掉"）。

现象：主 agent 第一轮 `工具面构建失败: 'function'`，`KeyError('function')`
来自 `schema["function"]["description"]`——插件工具 `to_llm_tool()` 返回的是
**扁平**形态 `{name, description, parameters}`（没有 `function` 包装），
旧实现直接下标访问 → 整个工具面构建失败 → 任务 `task_end=failed`（tools=0）。

契约：
1. 扁平形态被归一化成 OpenAI function 形态，`function.name` 一律用注册表名；
2. schema 推断抛异常 / 形态完全不可用的工具 → 跳过 + 告警，**其余能力照常可用**；
3. 拒绝 dangerous 档进直接工具面的硬规则不变。
"""

from __future__ import annotations

import pytest

from clipwright.tool import surface as sf
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.schema.tool import ToolExecResult, ToolStatus


class _FlatSchemaTool(BaseTool):
    """模拟插件工具：to_llm_tool 返回扁平形态。"""

    name = "__flat_schema_probe__"
    description = "扁平 schema 探针"

    async def execute(self, **kwargs):  # noqa: ANN003, ANN201
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name)

    def to_llm_tool(self, fmt: str = "openai"):  # noqa: ANN201
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
        }


class _BrokenSchemaTool(BaseTool):
    name = "__broken_schema_probe__"
    description = "schema 推断必抛"

    async def execute(self, **kwargs):  # noqa: ANN003, ANN201
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name)

    def to_llm_tool(self, fmt: str = "openai"):  # noqa: ANN201
        raise RuntimeError("boom")


@pytest.fixture()
def probe_tools(monkeypatch):
    from clipwright.services import capability_matrix as cm
    from clipwright.tool import register_builtin_tools

    if not ToolRegistry.list():
        register_builtin_tools()
    ToolRegistry.register(_FlatSchemaTool(), plugin_id="probe_plugin")
    ToolRegistry.register(_BrokenSchemaTool(), plugin_id="probe_plugin")
    monkeypatch.setitem(cm.TIER_MAP, "__flat_schema_probe__", "read")
    monkeypatch.setitem(cm.TIER_MAP, "__broken_schema_probe__", "read")
    try:
        yield
    finally:
        ToolRegistry.unregister_by_plugin("probe_plugin")


def test_flat_schema_is_normalised(probe_tools):
    entries = {e.name: e for e in sf.build_surface()}

    flat = entries.get("__flat_schema_probe__")
    assert flat is not None, "扁平 schema 的工具必须仍在工具面上"
    assert flat.schema["type"] == "function"
    assert flat.schema["function"]["name"] == "__flat_schema_probe__", "函数名用注册表名"
    assert flat.schema["function"]["parameters"]["properties"]["x"]["type"] == "string"
    assert "read" in flat.description, "档位提示要写进 description"


def test_broken_schema_tool_is_skipped_not_fatal(probe_tools):
    entries = {e.name for e in sf.build_surface()}

    assert "__broken_schema_probe__" not in entries, "坏 schema 的工具从工具面跳过"
    assert "__flat_schema_probe__" in entries, "其他工具不受影响"


def test_dangerous_tier_still_rejected():
    with pytest.raises(sf.SurfaceError, match="dangerous"):
        sf.build_surface(tiers=("read", "dangerous"))
