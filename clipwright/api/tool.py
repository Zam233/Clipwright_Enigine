"""工具 API — 查询、执行、批量操作原子能力。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from clipwright.schema.tool import ToolExecResult, ToolInfo
from clipwright.tool import ToolRegistry

router = APIRouter(prefix="/api/tool", tags=["tool"])


def _validate_tool_params(params: dict[str, Any]) -> None:
    """P0-4: 对 path / *_path 类字符串参数做白名单校验（URL 与不存在路径跳过）。"""
    from pathlib import Path as _Path

    from clipwright.security import SecurityViolation, assert_allowed_path

    for key, value in (params or {}).items():
        if not (key == "path" or key.endswith("_path")):
            continue
        if not isinstance(value, str) or not value:
            continue
        if value.startswith(("http://", "https://")):
            continue
        p = _Path(value)
        if not (p.is_absolute() or p.exists()):
            continue
        try:
            assert_allowed_path(p)
        except SecurityViolation as e:
            raise HTTPException(status_code=400, detail=f"参数 {key} 不在白名单目录: {e}") from e


@router.get("/list", response_model=list[ToolInfo])
async def list_tools() -> list[ToolInfo]:
    """列出所有已注册的原子能力工具及其可用状态。"""
    return ToolRegistry.list()


@router.post("/execute", response_model=ToolExecResult)
async def execute_tool(name: str, params: dict[str, Any] = {}) -> ToolExecResult:
    """按名称执行工具（P0-4: 路径类参数强制白名单校验）。"""
    _validate_tool_params(params)
    result = await ToolRegistry.execute(name, **params)
    if result.status == "not_found":
        raise HTTPException(status_code=404, detail=result.error)
    return result


@router.post("/batch", response_model=list[ToolExecResult])
async def batch_execute(calls: list[dict[str, Any]]) -> list[ToolExecResult]:
    """批量执行多个工具调用（顺序执行，互不影响；P0-4: 逐调用校验路径参数）。"""
    for call in calls or []:
        if isinstance(call, dict):
            _validate_tool_params({k: v for k, v in call.items() if k != "name"})
    return await ToolRegistry.execute_batch(calls)
