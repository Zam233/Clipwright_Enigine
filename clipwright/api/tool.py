"""工具 API — 查询、执行、批量操作原子能力。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from clipwright.schema.tool import ToolExecResult, ToolInfo
from clipwright.tool import ToolRegistry

router = APIRouter(prefix="/api/tool", tags=["tool"])


@router.get("/list", response_model=list[ToolInfo])
async def list_tools() -> list[ToolInfo]:
    """列出所有已注册的原子能力工具及其可用状态。"""
    return ToolRegistry.list()


@router.post("/execute", response_model=ToolExecResult)
async def execute_tool(name: str, params: dict[str, Any] = {}) -> ToolExecResult:
    """按名称执行工具。"""
    result = await ToolRegistry.execute(name, **params)
    if result.status == "not_found":
        raise HTTPException(status_code=404, detail=result.error)
    return result


@router.post("/batch", response_model=list[ToolExecResult])
async def batch_execute(calls: list[dict[str, Any]]) -> list[ToolExecResult]:
    """批量执行多个工具调用（顺序执行，互不影响）。"""
    return await ToolRegistry.execute_batch(calls)
