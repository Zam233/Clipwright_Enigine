"""子进程 worker 的结果/配置契约（C2 + 70KB 帧回归）。"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from clipwright.plugins.runtime.worker import _tool_result
from clipwright.schema.tool import ToolExecResult


def test_tool_exec_result_maps_to_success():
    """ToolExecResult 用 status；旧实现按 success 属性判断 → 恒 False。"""
    out = _tool_result(ToolExecResult(status="success", output={"a": 1}))
    assert out["success"] is True
    assert out["output"] == {"a": 1}


def test_tool_exec_result_error_status_maps_to_failure():
    out = _tool_result(ToolExecResult(status="error", error="boom"))
    assert out["success"] is False
    assert out["error"] == "boom"


def test_plain_dict_without_success_is_success_output():
    out = _tool_result({"value": 3})
    assert out["success"] is True
    assert out["output"] == {"value": 3}


def test_dict_with_status_is_honoured():
    out = _tool_result({"status": "error", "error": "nope"})
    assert out["success"] is False


@pytest.fixture
def subprocess_plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugins"
    pdir = root / "big_out"
    pdir.mkdir(parents=True)
    (pdir / "plugin.yaml").write_text(
        "id: big_out\nname: big\nkind: capability\nruntime: subprocess\n",
        encoding="utf-8",
    )
    (pdir / "config.yaml").write_text(
        "fields:\n  greeting:\n    value: hello-default\n", encoding="utf-8",
    )
    (pdir / "main.py").write_text(
        textwrap.dedent(
            """
            from clipwright.plugins.base import BasePlugin
            from clipwright.plugins.contribution import Contribution, ContributionKind
            from clipwright.schema.tool import ToolExecResult
            from clipwright.tool.base import BaseTool

            class BigTool(BaseTool):
                name = "big_blob"
                description = "returns 70KB + config"

                async def execute(self, **kwargs):
                    return ToolExecResult(
                        status="success",
                        output={
                            "blob": "x" * 70000,
                            "greeting": getattr(self, "config", {}).get("greeting"),
                        },
                    )

                def to_llm_tool(self):
                    return {"name": self.name, "description": "", "parameters": {}}

            class BigPlugin(BasePlugin):
                def contributions(self):
                    return [Contribution(ContributionKind.TOOL, "big_blob", BigTool())]
            """
        ),
        encoding="utf-8",
    )
    return pdir


@pytest.mark.asyncio
async def test_large_frame_and_source_config_reach_tool(subprocess_plugin: Path, tmp_path):
    """70KB 结果不能被 64KiB 行缓冲杀掉；源码默认配置要能到贡献对象。"""
    from clipwright.plugins.runtime.host import PluginHost

    host = PluginHost("big_out", subprocess_plugin, tmp_path / "data")
    host.start()
    try:
        result = await host.invoke(
            "tool.call", {"id": "big_blob", "input": {}}, timeout=30, scope={},
        )
    finally:
        host.stop()
    assert result["success"] is True, result
    assert len(result["output"]["blob"]) == 70000
    assert result["output"]["greeting"] == "hello-default"
