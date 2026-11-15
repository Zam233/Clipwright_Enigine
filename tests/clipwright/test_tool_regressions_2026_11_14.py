"""2026-11-14 工具审计修复的定向回归（P0/P1）。

覆盖：
- bpm_detect 误传 CompletedProcess 不再 AttributeError；
- int 参数推断成 JSON integer（旧实现回落 string）；
- 插件工具 `parameters_schema` 不被忽略（required 保留）；
- ToolRegistry 对 `output_path` 做白名单校验（旧实现可任意目录写盘）。
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.audio import _estimate_bpm_from_rms_stderr
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry


def test_bpm_helper_accepts_completed_process():
    """审计 P0：旧实现把 subprocess.CompletedProcess 传给 str 逻辑，必崩。"""
    proc = subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr="")
    assert _estimate_bpm_from_rms_stderr(proc) is None


def test_int_parameter_declared_as_integer():
    class _IntTool(BaseTool):
        name = "audit_int_param"
        description = "int param"
        permission_tier = "read"

        async def execute(self, top_k: int = 3, **kwargs):
            return ToolExecResult(status=ToolStatus.SUCCESS)

    schema = _IntTool().to_llm_tool("openai")
    assert schema["function"]["parameters"]["properties"]["top_k"]["type"] == "integer"


def test_parameters_schema_is_honored():
    class _DeclaredTool(BaseTool):
        name = "audit_declared_params"
        description = "declared params"
        parameters_schema = {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        }

        async def execute(self, **kwargs):
            return ToolExecResult(status=ToolStatus.SUCCESS)

    params = _DeclaredTool().to_llm_tool("openai")["function"]["parameters"]
    assert params["properties"]["prompt"]["type"] == "string"
    assert params["required"] == ["prompt"]


@pytest.mark.asyncio
async def test_registry_rejects_output_path_outside_roots():
    class _Writer(BaseTool):
        name = "audit_writer"
        description = "writer"
        permission_tier = "write_local"

        async def execute(self, output_path: str = "", **kwargs):
            return ToolExecResult(success=True, output_path=output_path)

    ToolRegistry.register(_Writer())
    try:
        outside = str(Path(tempfile.gettempdir()) / "audit_escape.mp4")
        result = await ToolRegistry.execute("audit_writer", output_path=outside)
        assert result.status == "error"
        assert "安全校验" in (result.error or "")
    finally:
        ToolRegistry.unregister("audit_writer")

