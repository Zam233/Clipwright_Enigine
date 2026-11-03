"""Kernel 工具面目录端点（路线 C / PRD §8.4 `GET /api/tools`）。

只做**目录**，不做执行：执行属于 Kernel 与执行网关的职责，给 HTTP 开一个
"随便调工具"的口子会把 F6 红线 1（一切副作用经治理层）判死。
运维/前端要看的是：有哪些能力、什么档位、可不可用、覆盖率多少。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/tools", tags=["tools-surface"])


@router.get("")
async def list_tools(
    request: Request,
    tier: str = Query(default="", description="按权限档过滤：read/write_local/costly"),
    available_only: bool = Query(default=False),
) -> dict:
    """工具面目录 + 覆盖度统计。"""
    from clipwright.tool.surface import SURFACE_TIERS, build_surface, surface_stats

    if tier and tier not in SURFACE_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的档位: {tier}（可选 {list(SURFACE_TIERS)}；dangerous 需经执行网关）",
        )
    tiers = (tier,) if tier else SURFACE_TIERS
    entries = [e for e in build_surface(tiers=tiers) if e.available or not available_only]
    return {
        "count": len(entries),
        "tiers": list(tiers),
        "stats": surface_stats(),
        "items": [e.to_public() for e in entries],
    }


@router.get("/schema")
async def list_tool_schemas(request: Request) -> dict:
    """LLM tool schema 视图（openai 格式），用于核对模型实际看到什么。"""
    from clipwright.tool.surface import schemas

    items = schemas()
    return {"count": len(items), "tools": items}


@router.get("/records")
async def list_call_records(request: Request, pipeline_id: str = "") -> dict:
    """工具调用审计（内存，最近 500 条；持久化在 C2 并入事件流）。"""
    from clipwright.tool.surface import records

    items = records(pipeline_id)
    return {"pipeline_id": pipeline_id, "count": len(items), "items": items}
