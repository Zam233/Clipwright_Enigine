"""P10: 插件治理 — M3 注册冲突告警 / M4 sys.modules 清理 测试。"""

from __future__ import annotations

import sys

from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry


class _DupTool(BaseTool):
    name = "dup_tool"
    description = "冲突测试工具"
    dependencies: list[str] = []

    async def execute(self, input_path: str = "", **kwargs):
        from clipwright.schema.tool import ToolExecResult, ToolStatus
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={})


def test_tool_register_conflict_warns(caplog) -> None:
    """M3/P1-2: 同名工具二次注册 → warning 日志（不静默吞掉）。"""
    import logging
    ToolRegistry._tools.pop("dup_tool", None)
    ToolRegistry.register(_DupTool(), plugin_id="plugin_a")
    with caplog.at_level(logging.WARNING, logger="clipwright"):
        ToolRegistry.register(_DupTool(), plugin_id="plugin_b")
    assert any("注册冲突" in r.message for r in caplog.records)
    ToolRegistry._tools.pop("dup_tool", None)


def test_purge_plugin_modules(tmp_path) -> None:
    """M4: reload 前清除插件模块，避免拿到旧代码。"""
    from clipwright.plugins.loader import PluginLoader

    loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
    # 模拟插件模块已导入
    sys.modules["fakeplug"] = object()
    sys.modules["fakeplug.main"] = object()
    sys.modules["fakeplug.helpers"] = object()
    sys.modules["other_mod"] = object()

    loader._purge_plugin_modules("fakeplug")
    assert "fakeplug" not in sys.modules
    assert "fakeplug.main" not in sys.modules
    assert "fakeplug.helpers" not in sys.modules
    assert "other_mod" in sys.modules
