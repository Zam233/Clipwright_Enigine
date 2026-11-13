"""通用能力网关 —— 插件对外提供 HTTP 入口的唯一通道。

**为什么是网关而不是插件自带 router**（PRD §15.1.5，2026-11-02 第四轮裁定）

`ContributionKind` 的 17 类里没有「HTTP 路由」这一类，新增贡献种类要走契约评审；
而「按能力名执行」这条通道 engine 本来就有（agent 侧的 `call_tool` 就是它）。所以
插件对外暴露的能力统一从网关走，插件不拥有 URL 空间。

**契约（PRD §15.1.5 表，W1 冻结）**

    POST /api/capability/{name}      body: {"params": {...}}
    200  ToolExecResult
    404  能力名解析不到（插件未装 / 被禁用）
    422  参数或路径不合规
    503  已解析到实现但依赖缺失（凭据未配置 / 上传器不可用）

**守则 ⑥（coding plan §4 唯一入口守则）**：本文件**不得**出现任何具体 provider 或
具体能力名的分支（不存在 `if name == "dub"`）。能力语义全部由注册表提供，网关只做
「解析 → 校验 → 执行 → 映射状态码」四步。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from clipwright.api.tool import _validate_tool_params
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool import ToolRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/capability", tags=["capability"])

#: 这些权限档位的调用必须留痕（PRD §15.1.5「权限与审计」）
_AUDITED_TIERS = frozenset({"costly", "dangerous"})


class _CapabilityRequest(BaseModel):
    """网关请求体。与 `/api/tool/execute` 的 `params` 同形，便于前端共用一套调用。"""
    params: dict[str, Any] = Field(default_factory=dict)


def _resolve(name: str) -> Any:
    """按能力名解析实现；解析不到 → 404（不是 500，也不是静默降级）。

    C1：这里用 ``resolve_any``（不筛可用性），把「装了但没配」交给
    `_assert_usable` 报 503——只筛可用会让 503 分支永远不可达，前端会把
    「去配凭据」误读成「去装插件」。
    """
    from clipwright.tool.capabilities import resolve_any

    tool = resolve_any(name)
    if tool is None:
        raise HTTPException(
            status_code=404,
            detail=f"能力 {name!r} 当前没有可用实现（对应插件未安装或被禁用）",
        )
    return tool


def _assert_usable(tool: Any, name: str) -> None:
    """已解析到实现但依赖缺失 → 503，与「解析不到」的 404 区分开。

    这条区分对前端很重要：404 意味着「去装插件」，503 意味着「去配凭据」。
    """
    from clipwright.tool.capabilities import is_usable

    if not is_usable(tool):
        raise HTTPException(
            status_code=503,
            detail=f"能力 {name!r} 的实现 {getattr(tool, 'name', '?')!r} 依赖未就绪"
                   "（凭据未配置或所需上传/运行依赖不可用）",
        )


@router.post("/{name}", response_model=ToolExecResult)
async def call_capability(
    name: str,
    req: _CapabilityRequest = Body(default=_CapabilityRequest()),
) -> ToolExecResult:
    """按**能力名**执行插件提供的实现。

    与 `/api/tool/execute` 的区别：后者按**工具名**直取，本端点先走能力解析
    （能力名优先、工具名兜底），所以插件装卸后能力名保持不变。
    """
    tool = _resolve(name)
    _assert_usable(tool, name)

    tool_name = str(getattr(tool, "name", "") or name)
    tier = str(getattr(tool, "permission_tier", "") or "")
    if tier in _AUDITED_TIERS:
        logger.info(
            "capability_call name=%s impl=%s tier=%s params=%s",
            name, tool_name, tier, sorted(req.params),
        )

    # 路径类参数强制白名单校验（与工具端点同一实现，只是状态码按网关契约给 422）
    _validate_tool_params(req.params, status_code=422)

    result = await ToolRegistry.execute(tool_name, **req.params)
    if result.status == ToolStatus.NOT_FOUND:
        # 解析到了但执行时被发现注册表里没有：说明中途被卸载，按缺失处理
        raise HTTPException(status_code=404, detail=result.error or f"能力 {name!r} 已不可用")
    if result.status == ToolStatus.DEPENDENCY_MISSING:
        raise HTTPException(status_code=503, detail=result.error or "依赖未就绪")
    return result
